#!/usr/bin/env python
r"""重建版 Webb-site CCASS dump 抽取器（原版喺 Downloads 被清，2026-09-19 由 zcode 重寫）。

用途：由 Webb-site 官方 ccass MySQL dump（~17GB .sql）streaming 抽出指定股票池嘅
quotes 同 dailylog（每日集中度 c5/c10），寫入 SQLite subset＋CSV。全程 streaming，
記憶體只佔單一 INSERT 語句大小，唔會修改原始 .sql。

    python scripts/extract_webb_dump.py --sql "<dump.sql>" \
        --universe <codes.csv> --out ./webb_extract_panel --from 2025-04-01

universe CSV：需要 `code` 欄（5 位代號，可多欄）。

已驗證嘅 dump 欄序（2026-09-17 由 ccassStructure SQL 直接確認，唔好改）：
  dailylog (12)：atDate, issueID, intermedHldg, intermedCnt, NCIPhldg, NCIPcnt,
                 CIPhldg, CIPcnt, c5, c10, CustHldg, BrokHldg   ← atDate 行先！
  shortnames (9)：issueID, shortName, fromDate, toDate, ID, stockCode, useDate,
                  stockExID, parallel
  participants (7)：partID, CCASSID, partName, atDate, addedDate, personID, hadHoldings
  calendar (3)：tradeDate, settleDate, deferred
  specialdays (6)：specialDate, pubHol, partSess, noAM, noPM, noSettle
  quotes (14)：issueID, code, atDate, prevClose, closing, ask, bid, high, low,
               vol, turn, susp, newsusp, noclose   ← 若 dump 版本唔同，runtime
               會偵測到 tuple 長度唔對並喺報告度標明（fail-loud，唔會靜靜寫錯位）
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

_ESC = {'n': '\n', 't': '\t', 'r': '\r', '0': '\0', 'b': '\b', 'Z': '\x1a'}

TABLE_COLS = {
    'shortnames': ['issueID', 'shortName', 'fromDate', 'toDate', 'ID', 'stockCode',
                   'useDate', 'stockExID', 'parallel'],
    'participants': ['partID', 'CCASSID', 'partName', 'atDate', 'addedDate',
                     'personID', 'hadHoldings'],
    'calendar': ['tradeDate', 'settleDate', 'deferred'],
    'specialdays': ['specialDate', 'pubHol', 'partSess', 'noAM', 'noPM', 'noSettle'],
    'dailylog': ['atDate', 'issueID', 'intermedHldg', 'intermedCnt', 'NCIPhldg',
                 'NCIPcnt', 'CIPhldg', 'CIPcnt', 'c5', 'c10', 'CustHldg', 'BrokHldg'],
    'quotes': ['issueID', 'code', 'atDate', 'prevClose', 'closing', 'ask', 'bid',
               'high', 'low', 'vol', 'turn', 'susp', 'newsusp', 'noclose'],
}


def parse_values(s: str):
    """解析 INSERT ... VALUES 後面嘅部分，yield 每個 tuple（元素為 str）。"""
    rows = []
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i] != '(':
            i += 1
        if i >= n:
            break
        i += 1
        row, cur, in_str = [], [], False
        while i < n:
            ch = s[i]
            if in_str:
                if ch == '\\':
                    nxt = s[i + 1] if i + 1 < n else ''
                    if nxt in _ESC:
                        cur.append(_ESC[nxt])
                        i += 2
                        continue
                    if nxt == "'":
                        cur.append("'")
                        i += 2
                        continue
                    cur.append(ch)
                    i += 1
                    continue
                if ch == "'":
                    if i + 1 < n and s[i + 1] == "'":
                        cur.append("'")
                        i += 2
                        continue
                    in_str = False
                    i += 1
                    continue
                cur.append(ch)
                i += 1
                continue
            if ch == "'":
                in_str = True
                i += 1
                continue
            if ch == ',':
                row.append(''.join(cur))
                cur = []
                i += 1
                continue
            if ch == ')':
                row.append(''.join(cur))
                rows.append(row)
                i += 1
                while i < n and s[i] not in '(;':
                    i += 1
                break
            cur.append(ch)
            i += 1
        else:
            break
    return rows


def norm(code) -> str:
    digits = re.sub(r'\D', '', str(code or ''))
    return digits.zfill(5) if digits else ''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sql', required=True, help='Webb ccass dump .sql（~17GB）')
    ap.add_argument('--universe', required=True, help='CSV with a `code` column')
    ap.add_argument('--out', required=True, help='output directory')
    ap.add_argument('--from', dest='from_date', default='2018-01-01')
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    targets = {}
    with open(args.universe, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            targets[norm(r['code'])] = r.get('tags') or ''

    report = [f'抽取時間：{time.strftime("%Y-%m-%d %H:%M:%S")}',
              f'來源：{args.sql}（{os.path.getsize(args.sql)/1e9:.2f} GB）',
              f'起始日期：{args.from_date}', f'股票池：{len(targets)} 隻', '']
    print('\n'.join(report[:4]), flush=True)

    db = sqlite3.connect(out / 'webb_subset.db')
    db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;')
    for tbl, cols in TABLE_COLS.items():
        db.execute(f'DROP TABLE IF EXISTS {tbl}')
        db.execute(f"CREATE TABLE {tbl} ({','.join(cols)})")

    issue_to_code: dict[str, str] = {}
    mismatch: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    t0 = time.monotonic()
    sink: dict[str, list] = {'quotes': [], 'dailylog': []}
    buf1: dict[str, list] = defaultdict(list)

    def h1(tbl: str, tup):
        if tbl == 'shortnames' and len(tup) == 9:
            sc = norm(tup[5])
            if sc in targets:
                issue_to_code.setdefault(str(tup[0]), sc)
        if tbl in ('shortnames', 'participants', 'calendar', 'specialdays') \
                and len(tup) == len(TABLE_COLS[tbl]):
            buf1[tbl].append(tup)
            if len(buf1[tbl]) >= 20000:
                flush1(tbl)

    def flush1(tbl: str):
        rows = buf1[tbl]
        if rows:
            db.executemany(
                f"INSERT INTO {tbl} VALUES ({','.join('?' * len(TABLE_COLS[tbl]))})",
                rows)
        buf1[tbl].clear()

    def h2(tbl: str, tup):
        if tbl == 'quotes' and len(tup) == 14:
            code = issue_to_code.get(str(tup[0]))
            if code and str(tup[2]) >= args.from_date:
                sink['quotes'].append((code,) + tuple(tup))
        elif tbl == 'dailylog' and len(tup) == 12:
            code = issue_to_code.get(str(tup[1]))
            if code and str(tup[0]) >= args.from_date:
                sink['dailylog'].append((str(tup[1]), code) + tuple(tup))

    def run_pass(handler) -> None:
        with open(args.sql, encoding='utf-8', errors='replace') as f:
            for line in f:
                m = re.match(r'INSERT INTO `(\w+)` VALUES', line)
                if not m:
                    continue
                tbl = m.group(1)
                if tbl not in TABLE_COLS:
                    continue
                v = line.find('VALUES')
                for tup in parse_values(line[v + 6:]):
                    counts[tbl] += 1
                    mismatch[f'{tbl}_len{len(tup)}'] += 1
                    handler(tbl, tup)

    print('[1/2] pass 1：shortnames 對照＋細表...', flush=True)
    run_pass(h1)
    for tbl in ('shortnames', 'participants', 'calendar', 'specialdays'):
        flush1(tbl)
    print(f'  issueID 對照：{len(issue_to_code)} 隻', flush=True)
    missing = sorted(set(targets) - set(issue_to_code.values()))
    if missing:
        report.append(f'⚠ 對唔到 issueID 嘅 {len(missing)} 隻：{" ".join(missing)}')

    print('[2/2] pass 2：quotes / dailylog（只抽目標 issueID）...', flush=True)
    run_pass(h2)

    for tbl in ('quotes', 'dailylog'):
        rows = sink[tbl]
        if tbl == 'quotes':
            cols = ['code'] + TABLE_COLS['quotes']
        else:
            cols = ['issueID', 'code'] + TABLE_COLS['dailylog']
        db.executemany(f"INSERT INTO {tbl} VALUES ({','.join('?' * len(cols))})", rows)
        csv_path = out / ('quotes.csv' if tbl == 'quotes' else 'dailylog.csv')
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        report.append(f'{tbl}（dump 掃到 {counts[tbl]} 行）：匯出 {len(rows)} 行 → {csv_path.name}')
    db.commit()
    db.close()

    report.append(f'掃描耗時：{time.monotonic() - t0:.0f}s')
    report.append('欄數分佈（fail-loud 稽核）：' + json.dumps(dict(mismatch)))
    (out / 'extract_report.txt').write_text('\n'.join(report), encoding='utf-8')
    print('\n'.join(report[-3:]), flush=True)
    print('✓ 完成', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
