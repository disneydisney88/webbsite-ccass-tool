if (typeof window.titleSearchConfig == 'undefined') {
    window.titleSearchConfig = {};
}
(function (m) {
    m.Language = 'ZH';
    m.HeadlineCategoryStart = new Date(2007, 5, 25); // 25 June 2007
    m.DocumentTypeStart = new Date(1999, 3, 1); // 1 April 1999
    m.DocumentTypeEnd = new Date(2007, 5, 24); // 24 June 2007
    m.JsonFileMimeType = "application/json; charset=UTF-8";
    m.TierOneUrl = "/ncms/script/eds/tierone_c.json";
    m.TierTwoUrl = "/ncms/script/eds/tiertwo_c.json";
    m.TierTwoGrpUrl = "/ncms/script/eds/tiertwogrp_c.json";
    m.ActiveStockUrl = "/ncms/script/eds/activestock_sehk_c.json";
    m.InactiveStockUrl = "/ncms/script/eds/inactivestock_sehk_c.json";
    m.DocUrl = "/ncms/script/eds/doc_c.json";
    if (window.location.hostname.toLowerCase() == "www.hkexnews.hk" || window.location.hostname.toLowerCase() == "sc.hkexnews.hk") {
        m.ActiveStockPopupUrl = "https://www.hkexnews.hk/stocklist_active_main_c.htm";
        m.InactiveStockPopupUrl = "https://www.hkexnews.hk/stocklist_delisted_main_c.htm";
        m.TitleSearchActionUrl = "https://www1.hkexnews.hk/search/titlesearch.xhtml";
        m.StockSearchPartialUrl = "https://www1.hkexnews.hk/search/partial.do?";
        m.StockSearchPrefixUrl = "https://www1.hkexnews.hk/search/prefix.do?";
    } else {
        m.ActiveStockPopupUrl = "stocklist_active_main_c.htm";
        m.InactiveStockPopupUrl = "stocklist_delisted_main_c.htm";
        m.TitleSearchActionUrl = "/search/titlesearch.xhtml";
        m.StockSearchPartialUrl = "/search/partial.do?";
        m.StockSearchPrefixUrl = "/search/prefix.do?";
    }
    m.Label_All = "所有";
    m.Label_StockCode = "股份代號";
    m.Label_StockShortName = "股份簡稱";
    m.Label_StockListTitle = "證券名單";
    m.Label_ViewAll = '更多';
	m.jsonFailMessage = "請重新載入頁面以取得最新資料: ";
    m.Interim = false;
    m.SearchDocAllMaxMonthRange = 1;
    m.SearchDocSingleMaxMonthRange = 12;
    m.PredictiveMinLength = 1;
    m.g_str_error_01 = "沒有您輸入的股份代號或股份名稱資料，或者所輸入的資料不正確，請重新輸入。";
    m.g_str_error_02 = "您所輸入的";
    m.g_str_error_03 = "不正確，請重新輸入。";
    m.g_str_error_04 = "日期";
    m.g_str_error_05 = "您所輸入的搜尋期間不正確，請重新輸入。";
    m.g_str_error_06 = "所輸入的";
    m.g_str_error_07 = "不可超過系統日期，請重新輸入。";
    m.g_str_error_08 = "如搜尋日期超過";
    m.g_str_error_09 = "個月，請選擇標題類別或文件類別或股份。";
    m.g_str_error_10 = "如搜尋日期超過";
    m.g_str_error_11 = "個月，請選擇股份。";
    m.g_str_error_12 = "股票號碼必須為數字 !";
    m.g_str_error_13 = "股票號碼必須為 4 位數字或以上 !";
	m.market = "SEHK";
    m.ViewMoreRecords = 1000;
    m.ViewMoreMessage = "只顯示1,000個搜尋結果。請輸入更多搜索資料。";

    // Interim Config
    if (m.Interim) {
        m.TitleSearchActionUrl = "http://www3.hkexnews.hk/listedco/listconews/advancedsearch/search_active_main_c.aspx";
        m.StockSearchPartialUrl = "http://www3.hkexnews.hk/listedco/listconews/advancedsearch/StockSearchPartial.ashx";
        m.StockSearchPrefixUrl = "http://www3.hkexnews.hk/listedco/listconews/advancedsearch/StockSearchPrefix.ashx";
    }

    // TESTING CONFIG
//    m.StockSearchPartialUrl = "eds/predictive/rest/stockSearch/partial";
//    m.StockSearchPrefixUrl = "eds/predictive/rest/stockSearch/prefix";
	$(document).trigger('titlesearchinit');
})(window.titleSearchConfig);
