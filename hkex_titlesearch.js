if (typeof window.LoadJSONData == 'undefined') {

    window.LoadJSONData = function (url, async) {
        if (typeof this.LoadStockDatadeferred == 'undefined') {
            this.LoadStockDatadeferred = {};
        }
        if (this.LoadStockDatadeferred[url]) {
            var state = this.LoadStockDatadeferred[url].state();
            if (state != 'reject') {
                return this.LoadStockDatadeferred[url].promise();
            }
        }
        this.LoadStockDatadeferred[url] = $.Deferred();
        var deferred = this.LoadStockDatadeferred[url];
				
        return $.ajax({
            url: url,
            dataType: 'json',
            async: async,
            mimeType: titleSearchConfig.JsonFileMimeType,
            cache: false
        }).done(function (data) {
            deferred.resolve(data);
        }).fail(function (xhr, textStatus, errorThrown) {
            deferred.reject();
				
			if(xhr.status != 200){	
				if(titleSearchConfig.Language.toLowerCase() == 'en'){
					alert('Please refresh the webpage to get latest data: '+url+ " [" + textStatus+"] " + "[" +errorThrown+"]" );	
				}else {
					alert('\u8acb\u91cd\u65b0\u8f09\u5165\u9801\u9762\u4ee5\u53d6\u5f97\u6700\u65b0\u8cc7\u6599: '+url+ " [" + textStatus+"] " + "[" +errorThrown+"]" );	
				}
			}
			
        });
        return deferred.promise();
    }
}
TitleSearchUtils = {};
TitleSearchUtils.isLeapYear = function (y) {
    return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
};
TitleSearchUtils.monthDiff = function (d1, d2) {
    var monthCount1 = d1.getFullYear() * 12 + d1.getMonth();
    var monthCount2 = d2.getFullYear() * 12 + d2.getMonth();
    return (monthCount2 - monthCount1);
};
TitleSearchUtils.daysInMonth = function (d) {
    var MonthDays = new Array(31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31);
    var m = d.getMonth();
    var y = d.getFullYear();
    if (m == 1 && TitleSearchUtils.isLeapYear(y)) {
        return 29;
    }
    return MonthDays[m];
};
TitleSearchUtils.validateDate = function (dateVal) {
    var regex = new RegExp(/^(\d{4})[\-\/](\d{2})[\-\/](\d{2})$/);
    var match = regex.exec(dateVal);
    if (match != null) {
        var y = parseInt(match[1], 10);
        var m = parseInt(match[2], 10) - 1;
        var d = parseInt(match[3], 10);
        var testDate = new Date(y, m, d);
        return (y == testDate.getFullYear() && m == testDate.getMonth() && d == testDate.getDate());
    }
    return false;
};
TitleSearchUtils.compareDateRange = function (d1, d2) {
    function compareArray(arrd1, arrd2) {
        if (arrd1[0] > arrd2[0]) return 1;
        else if (arrd1[0] < arrd2[0]) return -1;

        if (arrd1[1] > arrd2[1]) return 1;
        else if (arrd1[1] < arrd2[1]) return -1;

        if (arrd1[2] > arrd2[2]) return 1;
        else if (arrd1[2] < arrd2[2]) return -1;
        else return 0;
    }

    var arrd1 = [d1.getFullYear(), d1.getMonth(), d1.getDate()];
    var arrd2 = [d2.getFullYear(), d2.getMonth(), d2.getDate()];
    return compareArray(arrd2, arrd1);
};
TitleSearchUtils.isPosInteger = function (strVal) {
    if (!(TitleSearchUtils.isInteger(strVal))) return false;
    else if (strVal < 0) return false;
    else return true;
}
TitleSearchUtils.isInteger = function (strVal) {
    var bNum = TitleSearchUtils.isNumeric(strVal);
    if (bNum) {
        if (parseInt(strVal, 10) != strVal) return false;
    }
    return bNum;
}
TitleSearchUtils.isNumeric = function (strVal) {
    if (isNaN(strVal * 1)) return false;
    else return true;
}

function GetPreviousMonthDate(d) {
    var currentMonth = d.getMonth(),
        currentYear = d.getFullYear(),
        currentDate = d.getDate(),
        resultDate = new Date(d);
    if (currentMonth == 2 && currentDate > 28) {
        if (TitleSearchUtils.isLeapYear(currentYear)) {
            resultDate.setDate(29);
        } else {
            resultDate.setDate(28);
        }
    }
    resultDate.setMonth(currentMonth - 1);
    return resultDate;
}

function MakeHeadlineDropList(data, childData, parentId, checkIndex, level) {
    var $group = null, $list = null;
    $.each(data, function (k, v) {
        var s = v;
        if (s[checkIndex] && s[checkIndex] == parentId) {
            if ($group == null) {
                $group = $("<div/>").addClass("droplist-group droplist-submenu level" + level);
                $list = $("<ul/>").addClass("droplist-items").appendTo($group);
                if (level % 2 == 0) {
                    $list.addClass("even-col");
                }
                // ALL Item
                var all = CreateDropdownItemAll();
                all.attr('data-selectable', true);
                $list.append(all);
            }

            var item = CreateDropdownItem(v.code, v.name);

            if (childData) {
                $subgroup = MakeHeadlineDropList(childData, null, v.code, "t2Gcode", (level + 1));
                if ($subgroup) {
                    item.addClass("droplist-item-level-" + level);
                    item.append($subgroup);
                }
            }

            $list.append(item);
        }
    });
    return $group;
}

function CreateDropdownItem(val, label) {
    var $li = $('<li/>').addClass('droplist-item');
    $li.attr('data-value', val);
    $li.append($('<a href="#"></a>').html(label));
    return $li;
}

function CreateDropdownItemAll() {
    return CreateDropdownItem(-2, titleSearchConfig.Label_All);
}

function SetDropdownGroup($group, itemCallback, data, haveAll) {
    var listContainer = $('.combobox-boundlist .combobox-picker-list-wrap > .droplist-group', $group);
    listContainer.empty();
    listContainer.append('<ul class="droplist-items"></ul>');
    var $list = listContainer.children('.droplist-items');
    if (haveAll) {
        var allItem = CreateDropdownItemAll();
        allItem.attr('data-selectable', true);
        $list.append(allItem);
    }
    if (data && data.length > 0) {
        if (typeof itemCallback == 'function') {
            for (var i = 0; i < data.length; i++) {
                itemCallback.call(this, $list, data[i], i);
            }
        }
    }
    if (typeof $.fn.WidgetComboBoxField == 'function') {
        $group.WidgetComboBoxField('update');
    }
}

function FormatStockCodeNameValue(stockCode, stockName) {
    return [stockCode, stockName].join(' ');
}

function FormatDatePickerValue(d) {
    if (!d) {
        return '';
    }
    return [
        d.getFullYear(),
        StringPad(d.getMonth() + 1, 2),
        StringPad(d.getDate(), 2)
    ].join('/');
}

function StringPad(num, size) {
    var s = "000000000" + num;
    return s.substr(s.length - size);
}

function TitleSearchSectionWidget(interim) {
    this.interim = (interim == true);
    this.searchTypeEl = null;
    this.searchTypeCategroyEl = null;
    this.stockTypeEl = null;
    this.documentTypeEl = null;
    this.headlineCategoryEl = null;
    this.allTypeEl = null;
    this.stockCodeNameTextEl = null;
    this.stockCodeNameListEl = null;
    this.fromDateEl = null;
    this.toDateEl = null;
    this.applyButton = null;
    this.clearAllButton = null;
    this.popupStocksEl = null;
    this.newsTitleTextEl = null;
    this.tierOneText = null;
    this.tierTwoText = null;
    this.tierTwoGpText = null;
    this.docTypeText = null;
    this.selectedStock = '';
    this.today = new Date();
    this.form = document.TitleSearchPanel;
}

TitleSearchSectionWidget.prototype = {
	
	populatedBackendinfo: function () {		
		
		
		if($('#stockId').val() != null){
			this.form.stockId.value=$('#stockId').val();
		}
		if($('#stockCode').val() != null){
			this.selectedStock = $('#stockCode').val();
		}
	},
    init: function () {
        this.initElements();
        this.registerEvents();
        this.searchTypeEl.find('.droplist-item').attr('data-select-target', false).removeAttr('data-select-default').filter('[data-value="rbAll"]').attr('data-select-target', true);
        this.searchTypeEl.find('.combobox-field').attr('data-value', 'rbAll');
        
        if (this.interim) {
            this.SetInterimUI();
        }
        this._onSearchType();
        this.initDefaultValue();
        
        //get request's parameter 
        var parts = null;
        var from = null;
        var to = null;
        var stockCode = null;
        var searchType = null;
        var newsTitle = null;
        var tierTwoId = null;
        var securities = null;
        
        if($('input#tierTwoId').val() != null){
        	tierTwoId = $('input#tierTwoId').val();
        }
        
        if($('input#searchType').val() != null){
        	searchType = $('input#searchType').val();
        	if(searchType == "rbAll"){ 
                 this.SetSearchType(0);
        		 this.SetSearchTypeDDL(searchType);       		
                 this.allTypeEl.show();
                 this.UpdateReminderText(searchType);
        	}
        	if(searchType == "rbPrior2006"){
        		this.SetSearchType(2);
        		this.SetSearchTypeDDL(searchType);       		
        		this.SetDocumentTypeDDL();
        		this.documentTypeEl.show();
        		this.UpdateReminderText(searchType);
        	}
        	if(searchType == "rbAfter2006"){
        		this.SetSearchType(1);
        		this.SetSearchTypeDDL(searchType);
        		this.UpdateReminderText(searchType);
        		
        	}
        }
        
		
        if($('input#startDate').val() != null && $('input#startDate').val() != ''){
			var startDateStr = $('input#startDate').val();
            parts = startDateStr.split('-');
            from = new Date(parts[0], parts[1] - 1, parts[2]);
        	this.fromDateEl.val(FormatDatePickerValue(from)).change();
        }
		
        if($('input#endDate').val() != null && $('input#endDate').val() != ''){
			var endDateStr = $('input#endDate').val();
            parts = endDateStr.split('-');
        	to = new Date(parts[0], parts[1] - 1, parts[2]);
        	this.toDateEl.val(FormatDatePickerValue(to)).change();
        }
        
        if($('input#stockCode').val() != null){
        	stockCode = $('input#stockCode').val();
        	this.stockCodeNameTextEl.val(stockCode);
        	
        }else {
        	
        	this.stockCodeNameTextEl.val('');
        }
        
        if($('input#newsTitle').val() != null){
        	newsTitle = $('input#newsTitle').val();
        	this.newsTitleTextEl.val(newsTitle);
        }
        
        securities = $('input#selectedSecurities').val();
        if($('input#selectedSecurities').val() == ''){
        	securities = '0';
        }
        
    	this.stockTypeEl.find('.droplist-item[data-value="' + securities + '"]').first().click();
    	this.SetStockType(securities);
    	
    	this.stockTypeMobileEl.each(function () {
            var value = $(this).attr('value');
            if(value == securities.toUpperCase()){
            	$(this).prop("checked", true);
            }else {
            	$(this).prop("checked", false);
            }
        });
        
        $('#searchBarStockCode').append(stockCode);
        $('#searchBarDate').append(this.fromDateEl.val() + ' - ' + this.toDateEl.val());
        

        var	tierTwoId = $('input#tierTwoId').val();
        var tierOneId = $('input#tierOneId').val();
        var tierTwoGpId = $('input#tierTwoGpId').val();
        var	selectedDocType = $('input#selectedDocType').val();        
        this.SetHeadlineCategory(tierOneId, tierTwoId, tierTwoGpId);
        this.SetDocumentType(selectedDocType);
    },
    initDefaultValue: function () {
    	var dateparts;
        var stockId = this.GetStockId(),
            hasStockId = (typeof stockId != 'undefined' && stockId.length > 0);
        var searchType = this.searchTypeEl.find('.combobox-field').attr('data-value');
        var today = this.today,
            to = new Date(today),
            from = GetPreviousMonthDate(to);
        var currentFromString = this.fromDateEl.val(),
            currentToString = this.toDateEl.val();
        var defaultFrom = true, defaultTo = true;
        if (TitleSearchUtils.validateDate(currentFromString)) {
            defaultFrom = false;
            dateparts = currentFromString.split('/');
            from = new Date(dateparts[0], dateparts[1] - 1, dateparts[2]);            
        }
        if (TitleSearchUtils.validateDate(currentToString)) {
            defaultTo = false;
            dateparts = currentToString.split('/');
            to = new Date(dateparts[0], dateparts[1] - 1, dateparts[2]);
        }

        var _f = FormatDatePickerValue;
        var _tc = titleSearchConfig;
        var allDocDateMin = _f(_tc.DocumentTypeStart);
        var allDocDateMax = _f(_tc.DocumentTypeEnd);
        var headCatDateMin = _f(_tc.HeadlineCategoryStart);
        var todayStr = _f(today);
        var $dp = this.fromDateEl.add(this.toDateEl);

        switch (searchType) {
            case "rbPrior2006":
				if (hasStockId) {
					from = _tc.DocumentTypeStart;
					to = _tc.DocumentTypeEnd;
				} else {
					from = GetPreviousMonthDate(_tc.DocumentTypeEnd);
					to = _tc.DocumentTypeEnd;
				}
                $dp.attr('data-daterange-min', allDocDateMin);
                $dp.attr('data-daterange-max', allDocDateMax);
                break;
            case "rbAfter2006":
                var limitStart = _tc.HeadlineCategoryStart;
                if (this.interim === true) {
                    var codes = this.GetHeadlineCategory();
                    if (codes.length > 0 && parseInt(codes[0], 10) === -2) {
                        limitStart = _tc.DocumentTypeStart;
                        headCatDateMin = _tc.DocumentTypeStart;
                    }
                }

				if (hasStockId) {
					from = limitStart;
					to = new Date(today);
				} else {
					to = new Date(today);
					from = GetPreviousMonthDate(to);
				}

                $dp.attr('data-daterange-min', headCatDateMin);
                $dp.attr('data-daterange-max', todayStr);
                break;
            default:
                if (hasStockId) {
                    from = _tc.DocumentTypeStart;
                    to = new Date(today);
                }
                $dp.attr('data-daterange-min', allDocDateMin);
                $dp.attr('data-daterange-max', todayStr);
                break;
        }
        this.UpdateReminderText(searchType);
        this.fromDateEl.val(FormatDatePickerValue(from)).change();
        this.toDateEl.val(FormatDatePickerValue(to)).change();
        if (defaultFrom && from) {
            this.fromDateEl.addClass('active').attr('data-reset', FormatDatePickerValue(from));
        }
        if (defaultTo && to) {
            this.toDateEl.addClass('active').attr('data-reset', FormatDatePickerValue(to));
        }
        $dp.each(function () {
            var $this = $(this),
                min = $this.attr('data-daterange-min'),
                max = $this.attr('data-daterange-max');
            var o = {}, options = $this.data('options') || {};
            if (typeof max != 'undefined' && max.length > 0) {
                o.MAX = new Date(max);
            }
            if (typeof min != 'undefined' && min.length > 0) {
                o.MIN = new Date(min);
            }
            options = $.extend(options, o);
            $this.data("options", options);
        });
    },
    initElements: function () {
        var container = $('.filter__container-title-search');
        this.searchTypeEl = $('.combobox-group.searchType', container);
        this.searchTypeCategroyEl = $('.searchType-Categroy', container);
        this.documentTypeEl = $('#rbPrior2006', container);
        this.headlineCategoryEl = $('#rbAfter2006', container);
        this.allTypeEl = $('#rbAll', container);
        this.stockTypeEl = $('#selectedCategory', container);
        this.stockTypeMobileEl = $(':input[name="MB-Daterange"]', container);
        this.stockCodeNameTextEl = $('#searchStockCode', container);
        this.stockCodeNameListEl = $('#searchStockCodePicker', container);
        this.fromDateEl = $('#searchDate-From', container);
        this.toDateEl = $('#searchDate-To', container);
        this.applyButton = $('.filter__btn-applyFilters-js', container);
        this.clearAllButton = $('.btn-clearall', container);
        this.popupStocksEl = $('.popup-stocks-list', container);
        this.form.action = titleSearchConfig.TitleSearchActionUrl+'?lang='+titleSearchConfig.Language.toLowerCase();
        this.form.market.value = titleSearchConfig.market;
        
        this.newsTitleTextEl = $('#searchTitle', container);
        if (titleSearchConfig.Language) {
            container.attr('data-search-lang', titleSearchConfig.Language);
        }
    },
    SetInterimUI: function () {
        this.searchTypeEl.hide();
        this.searchTypeEl.find('.droplist-item').attr('data-select-target', false).removeAttr('data-select-default').filter('[data-value="rbAfter2006"]').attr('data-select-target', true);
        this.searchTypeEl.find('.combobox-field').attr('data-value', 'rbAfter2006');
        this.searchTypeCategroyEl.addClass('t1-fullwidth');
        this.headlineCategoryEl.addClass('theme-light');
        this.stockTypeEl.parent('.filter__radioGroup').hide();
        this.stockTypeEl.parent('.filter__radioGroup')
            .siblings('.filter__inputGroup,.filter__buttonGroup')
            .addClass('filter__noMargin');
        this.documentTypeEl.hide();
        this.headlineCategoryEl.show();
        this.allTypeEl.hide();
        this.searchTypeEl.closest('.form-input-text')
            .find('label[for="searchType"] .with-doctype')
            .hide();
    },
    initDocumentType: function() {
        function LoadDocData() {
            var deferred = $.Deferred();
            $.when(
                LoadJSONData(titleSearchConfig.DocUrl, true)
            ).done(function (data) {
                deferred.resolve(data);
            });
            return deferred.promise();
        }
        var that = this;
        $.when(LoadDocData())
            .done(function (data) {
                SetDropdownGroup(that.documentTypeEl, function ($list, itemData) {
                    var item = CreateDropdownItem(itemData.code, itemData.name);
                    $list.append(item);
                }, data, true);
                that.documentTypeEl.find('.droplist-item').first().attr('data-select-target', true).attr("data-select-default", true);
                
                var	tierTwoId = $('input#tierTwoId').val();
                var	tierOneId = $('input#tierOneId').val();
                var selectedDocType = $('input#selectedDocType').val();
                
                if(selectedDocType != '-1'){
            		that.documentTypeEl
                    .find('.droplist-item[data-value="' + selectedDocType + '"]')
                    .first()
                    .click();
            		this.docTypeText = that.documentTypeEl.find('.droplist-item[data-value="' + selectedDocType + '"]').first().children("a").text();  
            		
            		if(tierTwoId == '-2' && tierOneId == '-2'){
                		$('#searchBarCategory').append(this.docTypeText);
                	}
                }

            });
    },
    initHeadlineCategory: function() {
        function LoadTierTwoData() {
            var deferred = $.Deferred();
            $.when(
                LoadJSONData(titleSearchConfig.TierOneUrl, true),
                LoadJSONData(titleSearchConfig.TierTwoUrl, true),
                LoadJSONData(titleSearchConfig.TierTwoGrpUrl, true)
            ).done(function (tierOne, tierTwo, tierTwoGrp) {
                deferred.resolve({
                    tierOne: tierOne[0],
                    tierTwo: tierTwo[0],
                    tierTwoGroup: tierTwoGrp[0]
                });
            });
            return deferred.promise();
        }

        var that = this;
        $.when(LoadTierTwoData())
            .done(function (data) {
                var tierOne = data.tierOne;
                var tierTwoGroup = data.tierTwoGroup;
                var tierTwo = data.tierTwo;

                SetDropdownGroup(that.headlineCategoryEl, function ($list, itemData) {
                    var item = CreateDropdownItem(itemData.code, itemData.name);
                    var $t2group = MakeHeadlineDropList(tierTwoGroup, tierTwo, itemData.code, "t1code", 2);
                    if ($t2group == null) {
                        $t2group = MakeHeadlineDropList(tierTwo, null, itemData.code, "t1code", 2);
                    }
                    if ($t2group != null) {
                        item.addClass('droplist-item-level-1');
                        item.append($t2group);
                    }
                    $list.append(item);
                }, tierOne, true);
                that.headlineCategoryEl.find('.droplist-item').first().attr('data-select-target', true).attr("data-select-default", true);
                	
                var	tierTwoId = $('input#tierTwoId').val();
                var	tierOneId = $('input#tierOneId').val();
                var	tierTwoGpId = $('input#tierTwoGpId').val();
                var selectedDocType = $('input#selectedDocType').val();
                
                	if(tierTwoId != '-2'){
						
						that.headlineCategoryEl
	                    .find('.droplist-item[data-value="' + tierTwoGpId + '"]')
	                    .first()
	                    .click();
						
						that.headlineCategoryEl
	                    .find('.droplist-item[data-value="' + tierOneId + '"]')
	                    .first()
	                    .click();
	
	                	that.headlineCategoryEl
	                    .find('.droplist-item[data-value="' + tierTwoId + '"]')
	                    .first()
	                    .click();
                	}else if(tierTwoGpId != '-2'){
          		
	                	that.headlineCategoryEl
	                    .find('.droplist-item[data-value="' + 0 + '"]')
	                    .first()
	                    .click();
                		
	                	that.headlineCategoryEl
	                    .find('.droplist-item[data-value="' + tierTwoGpId + '"]')
	                    .first()
	                    .click();
                		
	                	that.headlineCategoryEl
	                    .find('.droplist-item[data-value="' + tierOneId + '"]')
	                    .first()
	                    .click();
	                	that.headlineCategoryEl.find('.combobox-body .combobox-field').text(that.headlineCategoryEl.find('.droplist-item[data-value="' + tierTwoGpId + '"]').first().children("a").text())
                	}else if(tierOneId != '-2'){
                		
	                	that.headlineCategoryEl
	                    .find('.droplist-item[data-value="' + tierOneId + '"]')
	                    .first()
	                    .click();
	                	
	                	that.headlineCategoryEl.find('.combobox-body .combobox-field').text(that.headlineCategoryEl.find('.droplist-item[data-value="' + tierOneId + '"]').first().children("a").text())
                	}
                	
                	this.tierOneText = that.headlineCategoryEl.find('.droplist-item[data-value="' + tierOneId + '"]').first().children("a").text();      	
                	this.tierTwoGpText = that.headlineCategoryEl.find('.droplist-item[data-value="' + tierTwoGpId + '"]').first().children("a").text(); 
                	this.tierTwoText = that.headlineCategoryEl.find('.droplist-item[data-value="' + tierTwoId + '"]').first().children("a").text(); 
                	         	
                	if(selectedDocType == '-1'){
                		if(tierTwoText == 'ALL' && tierTwoGpText == 'ALL'){ 
                			$('#searchBarCategory').append(this.tierOneText);
                		}else if(tierTwoText == 'ALL'){
                			$('#searchBarCategory').append(this.tierOneText + ' > ' + this.tierTwoGpText);
                		}else {
                			$('#searchBarCategory').append(this.tierOneText + ' > ' + this.tierTwoGpText + ' > ' + this.tierTwoText);
                		}
                    	
                    } 

            });
    },
    registerEvents: function () {
        if (document.readyState === "complete") {
            this._onReady();
        } else {
            $(document).on('ready.titleSearch', $.proxy(this._onReady, this));
        }
        this.clearAllButton.on('click.titleSearch', $.proxy(this._onReset, this));
        this.popupStocksEl.on('click.titleSearch', $.proxy(this._onPopup, this));
        this.stockCodeNameTextEl.on('change.titleSearch', $.proxy(this._onStockInputChange, this));
        this.stockCodeNameTextEl.on('stock-pick.titleSearch', $.proxy(function () {
            this.selectedStock = this.stockCodeNameTextEl.val();
            this.SetSearchFromDate();
        }, this));
        if (this.stockTypeEl.is('.combobox-group')) {
            this.stockTypeEl.find('.combobox-field').on('change.titleSearch', $.proxy(this._onCategory, this));
        } else {
            this.stockTypeEl.on('change.titleSearch', $.proxy(this._onCategory, this));
        }
    },
    getStockType: function () {
        if (this.stockTypeEl.is('.combobox-group')) {
            return this.stockTypeEl.find('.combobox-field').attr('data-value');
        } else {
            return this.stockTypeEl.filter(':checked').val();
        }
    },
    
    SetStockType: function (value) {
        this.form.category.value = value;
    },
    SetSearchType: function (value) {
        this.form.searchType.value = value;
    },
    GetSearchType: function () {
        return this.form.searchType.value;
    },
    SetDocumentType: function (value) {
        this.form.documentType.value = value;
    },
    GetDocumentType: function () {
        return this.form.documentType.value;
    },
    SetHeadlineCategory: function (T1code, T2code, T2Gcode) {
        this.form.t1code.value = T1code;
        this.form.t2code.value = T2code;
        this.form.t2Gcode.value = T2Gcode;

        if (this.interim === true) {
            this.initDefaultValue();
        }
    },
    GetHeadlineCategory: function() {
        return [this.form.t1code.value, this.form.t2Gcode.value, this.form.t2code.value];
    },
    SetStockId: function(stockId) {
        this.form.stockId.value = stockId;
    },
    GetStockId: function () {
    	if(this.form.stockId.value == '-1'){
    		return '';
    	}
        return this.form.stockId.value;
    },
    SetSearchFromDate: function () {
        (function (that, c, formater) {
            var searchType = parseInt(that.GetSearchType(), 10);
			var from, to;
            if (searchType === 0) {
                from = formater(c.DocumentTypeStart);
				to = formater(new Date(that.today));
                that.fromDateEl.val(from).change();
				that.toDateEl.val(to).change();
            } else if (searchType == 1 && that.interim === true) {
                var codes = that.GetHeadlineCategory();
                if (codes.length > 0 && parseInt(codes[0], 10) === -2) {
                    from = formater(c.DocumentTypeStart);
                    that.fromDateEl.val(from).change();
                }
			} else if (searchType == 1 && that.interim === false) {
                from = formater(c.HeadlineCategoryStart);
				to = formater(new Date(that.today));
                that.fromDateEl.val(from).change();
				that.toDateEl.val(to).change();				
            } else if (searchType == 2) {
				to = formater(c.DocumentTypeEnd);
				from = formater(c.DocumentTypeStart);
                that.fromDateEl.val(from).change();
				that.toDateEl.val(to).change();
			}
        }(this, titleSearchConfig, FormatDatePickerValue));
    },
    setupPredictiveSearch: function () {
        function LoadStockData(url, stockType, name, type) {
            var mapping = ['A', 'I'];
            var requestParam = {
                lang: titleSearchConfig.Language,
                type: mapping[stockType],
                name: name,
				market: titleSearchConfig.market

            };
			
			
            return $.ajax({
                url: url,
                dataType: 'jsonp',
                data: requestParam,
                jsonp: "callback",
                jsonpCallback: 'callback'
            }).fail(function (xhr, textStatus, errorThrown) {
			
				if(xhr.status != 200){
					if(titleSearchConfig.Language.toLowerCase() == 'en'){
						alert('Please refresh the webpage to get latest data: '+url+ " [" + textStatus+"] " + "[" +errorThrown+"]" );	
					}else {
						alert('\u8acb\u91cd\u65b0\u8f09\u5165\u9801\u9762\u4ee5\u53d6\u5f97\u6700\u65b0\u8cc7\u6599: '+url+ " [" + textStatus+"] " + "[" +errorThrown+"]" );	
					}
				}
			});
        }

        var that = this;
        var prefixDeferred, partialDeferred;
        this.stockCodeNameTextEl.HkexAutoComplete({
            inputDelay: 1000,
            viewAllReplace: true,
            showViewAll: true,
            viewAllText: titleSearchConfig.Label_ViewAll,
            viewall_remotedata: function (val) {
                var listId = that.getStockType();
                if (partialDeferred) {
                    var state = partialDeferred.state();
                    if (state == 'pending') {
                        partialDeferred.reject();
                    }
                }
                partialDeferred = $.Deferred();
                if (val.length < titleSearchConfig.PredictiveMinLength) {
                    partialDeferred.reject();
                    return partialDeferred.promise();
                }
                $.when(
                    LoadStockData(titleSearchConfig.StockSearchPartialUrl, listId, val, 1)
                ).done(function (x) {
                    var ret = x.stockInfo;
                    if (ret.length > 0) {
						if (titleSearchConfig.ViewMoreRecords && ret.length > titleSearchConfig.ViewMoreRecords) {
                            ret = ret.slice(0, titleSearchConfig.ViewMoreRecords).map(function (v) {
                                return { id: v.stockId, sym: v.code, nm: v.name };
                            });
                            that.showViewMoreMessage = true;
                        } else {
                            ret = ret.map(function (v) {

                                return { id: v.stockId, sym: v.code, nm: v.name };
                            });
                            that.showViewMoreMessage = false;
                        }
                    }
                    partialDeferred.resolve({ data: { stocklist: ret } });
                });
                return partialDeferred.promise();
            },
            onValueSelected: function (data) {
                var codeName = FormatStockCodeNameValue(data.sym, data.nm);
                that.SetStockId(data.id);
                that.selectedStock = codeName;
                that.SetSearchFromDate();
                return codeName;
            },
            remotedata: function (val) {
                var listId = that.getStockType();
                if (prefixDeferred) {
                    var state = prefixDeferred.state();
                    if (state == 'pending') {
                        prefixDeferred.reject();
                    }
                }
                prefixDeferred = $.Deferred();
                if (val.length < titleSearchConfig.PredictiveMinLength) {
                    prefixDeferred.reject();
                    return prefixDeferred.promise();
                }
                $.when(
                    LoadStockData(titleSearchConfig.StockSearchPrefixUrl, listId, val, 0)
                ).done(function (x) {
                    var ret = x.stockInfo;
                    if (ret.length > 0) {
                        ret = ret.map(function (v) {
                            return { id: v.stockId, sym: v.code, nm: v.name };
                        });
                    }
                    prefixDeferred.resolve({ data: { stocklist: ret } });
                });
                return prefixDeferred.promise();
            }
        });

		// Enchance suggestions
        this.viewMoreMessage = $("<div class=\"view-more-msg\" style=\"padding: 5px;border-top:1px solid #cbcbcb;\"></div>");
        this.viewMoreMessage.text(titleSearchConfig.ViewMoreMessage);
        var viewMoreStyle = $('<style>.view-more-msg {display: none;} .viewall-suggestions .view-more-msg {display: block;} .viewall-suggestions .view-more-msg.view-more-msg-hide {display: none;}</style>');
        $('html > head').append(viewMoreStyle);
        // Unbind focus on inputbox
        var that = this;
        var suggestions = that.stockCodeNameTextEl.closest('.autocomplete-group').children('.autocomplete-suggestions');
		suggestions.append(this.viewMoreMessage);
        this.stockCodeNameTextEl
            .off('focus.exAutoComplete')
            .on('focus.exAutoComplete', function () {
                var stockId = that.GetStockId();
                var resultsCount = suggestions.find('table tr').length;
                if (!stockId && resultsCount > 0) {
                    that.stockCodeNameTextEl.trigger("input");
                }
            })
            .on('complete.va.exAutoComplete', function () {
                var resultsCount = suggestions.find('table tr:not(.suggestion-viewall)').length;
                if (resultsCount === 0) {
                    suggestions.hide();
                    alert(titleSearchConfig.g_str_error_01);
                }
				if (that.showViewMoreMessage) {
                    that.viewMoreMessage.removeClass('view-more-msg-hide');
                } else {
                    that.viewMoreMessage.addClass('view-more-msg-hide');
                }
            });
    },
    ToggleTitleSearchStockPicker: function (show) {
        if (show == true) {
            this.stockCodeNameListEl.show();
            this.stockCodeNameTextEl.closest('.autocomplete-group')
                .add(this.stockCodeNameTextEl)
                .hide();
        } else {
            this.stockCodeNameListEl.hide();
            this.stockCodeNameTextEl.closest('.autocomplete-group')
                .add(this.stockCodeNameTextEl)
                .show();
        }
    },
    DoPartialSearch: function (searchVal) {
        var that = this;
        var selectSingleRecord = function (skipError) {
            var items = suggestionsList.find('table tr.autocomplete-suggestion:not(.suggestion-viewall)');
            if (items.length == 1) {
                items.first().click();
                that._onSearch();
            } else if (items.length == 0) {
                if (!skipError) {
                    alert(titleSearchConfig.g_str_error_01);
                }
            } else {
                that.stockCodeNameTextEl.trigger("input");
            }
        }
        var suggestionsList = this.stockCodeNameTextEl.closest('.autocomplete-group')
            .find('.autocomplete-suggestions');
        if (suggestionsList.is('.viewall-suggestions')) {
            selectSingleRecord();
        } else {
            var eventName = 'complete.va.exAutoComplete.titleSearch';
            that.stockCodeNameTextEl.off(eventName).on(eventName, function (e) {
                that.stockCodeNameTextEl.off(eventName);
                selectSingleRecord(true);
            });
            this.stockCodeNameTextEl.trigger('viewall');
        }
        
        return true;
    },
    FillPartialSearchResult: function (data) {
        var that = this;
        $('.combobox-field', this.stockCodeNameListEl).off('change.titleSearch');
        $('.combobox-field', this.stockCodeNameListEl)
            .on('change.titleSearch', function () {
                var value = $(this).attr('data-value');
                var name = $(this).text();
                that.SetStockId(value);
            });

        this.ToggleTitleSearchStockPicker(true);
        SetDropdownGroup(this.stockCodeNameListEl, function ($list, itemData, index) {
            var item = CreateDropdownItem(itemData.stockId, FormatStockCodeNameValue(itemData.code, itemData.name));
            $list.append(item);
            if (index == 0) {
                item.attr('data-select-target', true);
            }
        }, data, false);
        if (data.length == 1) {
            this._onSearch();
        }
    },
    UpdateReminderText: function(searchType) {
        var reminderClass = '.' + searchType + '-reminder';
        $('.filter__container-title-search .reminder')
            .children().addClass('reminder-hidden')
            .filter(reminderClass).removeClass('reminder-hidden');
        var visibleReminder = $('.filter__container-title-search .reminder')
            .children(':visible').length;
        if (visibleReminder > 0) {
            $('.filter__container-title-search').removeClass('reminder-invisible');
        } else {
            $('.filter__container-title-search').addClass('reminder-invisible');
        }
    },
    SetSearchTypeDDL: function (value) {
        var mapping = ["rbAll", "rbAfter2006", "rbPrior2006"];      
//        if (mapping.map(value)) {
//            var ddl_value = mapping[value];
            this.searchTypeEl
                .find('.droplist-item[data-value="' + value + '"]')
                .first()
                .click();
//        }

    },
    ResetHeadlineDDL: function() {
        this.SetHeadlineDDL(-2);
        this.SetHeadlineCategory('', '', '');
    },
    SetHeadlineDDL: function (value) {
        this.headlineCategoryEl
                .find('.droplist-item[data-value="' + value + '"]')
                .first()
                .click();
    },
    ResetDocumentTypeDDL: function () {
        this.SetDocumentTypeDDL(-2);
        this.SetDocumentType('');
    },
    SetDocumentTypeDDL: function (value) {
        this.documentTypeEl
                .find('.droplist-item[data-value="' + value + '"]')
                .first()
                .click();
    },
    SetCategoryDDL: function (value) {
        this.documentTypeEl
                .find('.droplist-item[data-value="' + value + '"]')
                .first()
                .click();
    },
    _onReady: function () {
        // Set Predictive Search
        this.setupPredictiveSearch();
        // Init Dropdown
        this.initHeadlineCategory();
        this.initDocumentType();

        // Listen Change on Search Type
        var that = this;
        this.searchTypeEl.find('.combobox-field').off('change').on('change.titleSearch', $.proxy(this._onSearchType, this));
        this.documentTypeEl.find('.combobox-field').off('change').on('change.titleSearch', $.proxy(this._onDocumentType, this));
        this.headlineCategoryEl.find('.combobox-field').off('change').on('change.titleSearch', $.proxy(this._onHeadlineCategory, this));

        // Listen on Search Button
        this.applyButton.on('click.titleSearch', $.proxy(this._onSearch, this));
    },
    
    _onCategory: function (e) {
        var v = this.stockTypeEl.find('.combobox-field').attr('data-value');
        
        this.SetStockType(v);   
		this.stockCodeNameTextEl.trigger('clearlast');
    },
    _onDocumentType: function (e) {
        var v = this.documentTypeEl.find('.combobox-field').attr('data-value');
        this.SetDocumentType(v);
        this.stockCodeNameTextEl.trigger('hidelist');
    },
    _onHeadlineCategory: function (e) {
        var T1code = "-2", T2code = "-2", T2Gcode = "-2";
        var selected = this.headlineCategoryEl.find('[data-select-target="true"]').first();
        var options = selected.parents('[data-value]').add(selected);
        options.each(function (index, e) {
            var value = $(this).attr('data-value');
            if (index == 0) {
                T1code = value;
            } else if (index == 1) {
                T2code = value;
            } else if (index == 2) {
                T2Gcode = T2code;
                T2code = value;
            }
        });
        this.SetHeadlineCategory(T1code, T2code, T2Gcode);
        this.stockCodeNameTextEl.trigger('hidelist');
    },
    _onSearchType: function (e) {
        var searchType = this.searchTypeEl.find('.combobox-field').attr('data-value');

        switch (searchType) {
            case "rbPrior2006":
                this.SetSearchType(2);
                this.documentTypeEl.show();
                this.headlineCategoryEl.hide();
                this.allTypeEl.hide();
                this.ResetHeadlineDDL();
                this.SetDocumentTypeDDL(-2);
                break;
            case "rbAfter2006":
                this.SetSearchType(1);
                this.documentTypeEl.hide();
                this.headlineCategoryEl.show();
                this.allTypeEl.hide();
                this.ResetDocumentTypeDDL();
                this.SetHeadlineDDL(-2);
                break;
            default:
                this.SetSearchType(0);
                this.documentTypeEl.hide();
                this.headlineCategoryEl.hide();
                this.allTypeEl.show();
                this.ResetHeadlineDDL();
                this.ResetDocumentTypeDDL();
                break;
        }
        // this.UpdateReminderText(searchType);
        this.initDefaultValue();
    },
    _onReset: function (e) {
        if (e) {
            e.preventDefault();
        }

        if (this.stockCodeNameListEl.is(':visible')) {
            this.FillPartialSearchResult([]);
            this.ToggleTitleSearchStockPicker(false);
        }
        this.stockCodeNameTextEl.val('').keyup();
        this.SetStockId('');
        this.SetSearchTypeDDL('rbAll');  
        this.fromDateEl.val('').change();
        this.toDateEl.val('').change();
        setTimeout($.proxy(this._onSearchType, this), 0);
    },
    preprocessMainForm: function () {
        var fromStr = this.fromDateEl.val();
        var toStr = this.toDateEl.val();
        var stockId = this.GetStockId();
        var _tc = titleSearchConfig;

        /*
        if (stockId != '' && TitleSearchUtils.isInteger(stockId) == false) {
            alert(titleSearchConfig.g_str_error_01);
            return false;
        }
        */

        if (TitleSearchUtils.validateDate(fromStr) == false) {
            alert(_tc.g_str_error_02 + _tc.g_str_error_04 + _tc.g_str_error_03);
            return false;
        }

        if (TitleSearchUtils.validateDate(toStr) == false) {
            alert(_tc.g_str_error_02 + _tc.g_str_error_04 + _tc.g_str_error_03);
            return false;
        }

        var from = new Date(fromStr);
        var to = new Date(toStr);

        if (TitleSearchUtils.compareDateRange(from, to) < 0) {
            alert(_tc.g_str_error_05);
            return false;
        }
        if (TitleSearchUtils.compareDateRange(from, this.today) < 0) {
            alert(_tc.g_str_error_06 + _tc.g_str_error_04 + _tc.g_str_error_07);
            return false;
        }
        if (TitleSearchUtils.compareDateRange(to, this.today) < 0) {
            alert(_tc.g_str_error_06 + _tc.g_str_error_04 + _tc.g_str_error_07);
            return false;
        }

        this.form.from.value = fromStr.replace(/\//g, '');
        this.form.to.value = toStr.replace(/\//g, '');

        if (stockId == '') {
            if (this.validateDateAllDocTypeSearchPeriod(from, to) == false) {
                return false;
            }
        }   
        
        if(hkexApp.utils.isTablet() || hkexApp.utils.isDesktop()){
        	this._onCategory();
        } else{
        	var v = this.stockTypeMobileEl.filter(':checked').val(); 	
            this.SetStockType(v);   
        }

        return true;
    },
    _onSearch: function (e) {
        if (e) {
            e.preventDefault();
        }
        if (this.applyButton.is('.btn-disable')) {
            // do nothing;
            return;
        }
        var selectedStockId = this.GetStockId();
        if (!selectedStockId) {
            var inputedText = this.stockCodeNameTextEl.val().trim();
            if (inputedText && inputedText.length > 0) {
                if (e) {
                    e.stopPropagation();
                }
                this.DoPartialSearch(inputedText);
                return;
            }
        }
        
        if (this.preprocessMainForm()) {
            this.form.submit();
        }
    },
    _onPopup: function (e) {
        if (e) {
            e.preventDefault();
        }
        var stockType = this.getStockType();
        var url = titleSearchConfig.ActiveStockPopupUrl;
        if (stockType == 1) {
            url = titleSearchConfig.InactiveStockPopupUrl;
        }
        window.open(url, 'lci-popup', 'directories=no,menubar=no,scrollbars=yes,status=no,toolbar=no,height=400,width=400');
    },
    _onStockInputChange: function (e) {
        var val = $(e.target).val();
        var today = this.today;
		var from, to;
        var searchType = this.searchTypeEl.find('.combobox-field').attr('data-value');
        var _tc = titleSearchConfig;
        if (val != this.selectedStock) {
            this.SetStockId('');
            this.selectedStock = '';
			if (val == '') {
				switch (searchType) {
				case "rbPrior2006":
					from = GetPreviousMonthDate(_tc.DocumentTypeEnd);
					to = _tc.DocumentTypeEnd;
					break;
				case "rbAfter2006":
					to = new Date(today);
					from = GetPreviousMonthDate(to);
					break;
				default:
					to = new Date(today);
					from = GetPreviousMonthDate(to);
					break;
			}
			this.fromDateEl.val(FormatDatePickerValue(from)).change();
			this.toDateEl.val(FormatDatePickerValue(to)).change();
			}
        }
    }
};

TitleSearchSectionWidget.prototype.validateDateAllDocTypeSearchPeriod = function (from, to) {
    var _tc = titleSearchConfig;
    var p_fromDate = from.getDate();
    var p_toDate = to.getDate();
    var mdiff = TitleSearchUtils.monthDiff(from, to);
    var p_lastdayfrom = TitleSearchUtils.daysInMonth(from);
    var searchType = parseInt(this.GetSearchType(), 10);
    var SearchDocAllMaxMonthRange = _tc.SearchDocAllMaxMonthRange;
    var SearchDocSingleMaxMonthRange = _tc.SearchDocSingleMaxMonthRange;
    var allErrorMessage = _tc.g_str_error_08 + SearchDocAllMaxMonthRange + _tc.g_str_error_09;
    var singleErrorMessage = _tc.g_str_error_10 + SearchDocSingleMaxMonthRange + _tc.g_str_error_11;

    if (searchType == 2) {
        // Document Type
        var documentType = parseInt(this.GetDocumentType(), 10);
        if (documentType == -2 && (mdiff > SearchDocAllMaxMonthRange || (mdiff == SearchDocAllMaxMonthRange && p_fromDate < Math.min(p_lastdayfrom, p_toDate)))) {
            alert(allErrorMessage);
            return false;
        } else if (documentType != -2 && (mdiff > SearchDocSingleMaxMonthRange
				|| (mdiff == SearchDocSingleMaxMonthRange && p_fromDate < Math.min(p_lastdayfrom, p_toDate)))) {
            alert(singleErrorMessage);
            return false;
        }
    } else if (searchType == 1) {
        // Headline
        var headlineTypes = this.GetHeadlineCategory();
        var tier_1_value = parseInt(headlineTypes[0], 10);
        if (tier_1_value == -2 && (mdiff > SearchDocAllMaxMonthRange || (mdiff == SearchDocAllMaxMonthRange && p_fromDate < Math.min(p_lastdayfrom, p_toDate)))) {
            alert(allErrorMessage);
            return false;
        } else if (tier_1_value != -2 && (mdiff > SearchDocSingleMaxMonthRange
				|| (mdiff == SearchDocSingleMaxMonthRange && p_fromDate < Math.min(p_lastdayfrom, p_toDate)))) {
            alert(singleErrorMessage);
            return false;
        }
    } else {
        // ALL
        if (mdiff > SearchDocAllMaxMonthRange || (mdiff == SearchDocAllMaxMonthRange && p_fromDate < Math.min(p_lastdayfrom, p_toDate))) {
            alert(allErrorMessage);
            return false;
        }
    }
    return true;
}

var titleSearchSection = new TitleSearchSectionWidget(titleSearchConfig.Interim);
titleSearchSection.populatedBackendinfo();
titleSearchSection.init();

if ( $(".news-hkex .content-container").css("display") == "none") 
{
	$(".news-hkex #hkex_news_topbanner .banner__container .banner__pageheading").removeClass('visibility_visible');
	$(".news-hkex #hkex_news_topbanner .banner__container .banner__pageheading").removeClass('visibility_hidden').addClass('visibility_hidden');
}else{
	
	$(".news-hkex #hkex_news_topbanner .banner__container .banner__pageheading").removeClass('visibility_hidden');
	$(".news-hkex #hkex_news_topbanner .banner__container .banner__pageheading").removeClass('visibility_visible').addClass('visibility_visible');
}

if(window.location.href.indexOf("gem") <= -1) {
	$('.filter__container-title-search .popup-stocks-list').addClass('visibility_hidden');
}


function InitListedCompanyInfoFancyNote() {
    $(function () {
        $('#lci-fancynote-container').appendTo('body');
    });
}
InitListedCompanyInfoFancyNote();
