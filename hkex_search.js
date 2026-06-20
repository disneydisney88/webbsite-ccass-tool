// Mobile popup
var noteTpl = [
'<div class="fancy-note-popup-wrap fancy-note">',
'<div class="fancy-note-popup" id="headline-more">',
'<a class="fancy-note-close" href="#"></a>',
'<div class="fancy-note-detail" style="padding-top: 55px">',
'<div class="fancy-note-desc"></div>',
'</div>',
'</div>',
'<div class="fancy-note-shade"></div>',
'</div>'
].join('');
$('body').append(noteTpl);
$.fn.initFancyNote();

//Strip Whitespace
$('.SR-filter__container').CleanWhitespace();

// Search Bar
var SEARCH_BAR_LCI_TITLE = { key: '.title-search-search-bar', container: '.filter__container-title-search', initDatePicker: false };
$.fn.searchBarInit(SEARCH_BAR_LCI_TITLE.key, SEARCH_BAR_LCI_TITLE.container, SEARCH_BAR_LCI_TITLE.initDatePicker);
$('.title-search-search-bar').stickySearchBar();

// Mobile Toolbar
$(".PD_MB_filter").moreFilter({
    headlineSelector: '.title-search-result td .headline,.title-search-result .document-details .headline'
});

// Mobile List
$('.title-search-result-toolbar:not(.LLCI-toolbar)').SearchResultToolbar({
    tableSelector: '.title-search-result table.table',
    headlineSelector: '.title-search-result td .headline,.title-search-result .document-details .headline'
});
// Listen on dropdown change
$('.titlesearch-header-section .title-search-search-bar #tier1-select .combobox-field').on('change', function () {
    var value = $(this).attr('data-value');
    var $input = $(this).closest('.form-input-text');
    $input.children('.reminder').children('span').addClass('reminder-hidden')
        .filter('.' + value + '-reminder').removeClass('reminder-hidden');
});
// Listen on Change
$('table.table').on('contentupdate', function () {
//    $('table.table').trigger('update');
    table_tooltip();
});
// Make Table Sort
$('table.table').tablesorter({
    sortMultiSortKey: null,
    numberSorter: function () {
        return 0;
	},
	textSorter: function () {
	        return 0;
	},

    onRenderHeader: function (index) {
        var _this = this;
        if (_this.hasClass('headline-toggle')) {
            $('.hideHeadline').first().clone(true).appendTo(this);
        }
    }
});
// Make Table scrollable
$('table.table.table-scroll').scrollableTable({
    tableWidth: 1000
});

// Make Table sticky
// $.fn.initStickyHeader();
$('table.table:not(".tablesorter-stickyHeader")').trigger('applyWidgetId', ['stickyHeaders']);
$('table.table.tablesorter-stickyHeader').on("click.sorter", "th:not(.sorter-false)", function() {
	$(window).scrollTop(0);
});

// Add Listener to Global Events
$('body').on('fontSizeChange', function () {
    $('table.table.table-scroll').scrollableTable("forcerefresh");
});
//last desktop popup show in top
function table_tooltip() {
    $('.headline .tooltip').off('mouseenter.table_tooltip').on('mouseenter.table_tooltip', function () {
        if ($(this).parent().parent().parent().is('td')) {
            var $tr = $(this).closest('tr');
            if ($tr.next('tr').length > 0) {
                $(this).parent().parent().find(".fancy-note-hover").removeClass("top").addClass("bottom");
            } else {
                $(this).parent().parent().find(".fancy-note-hover").removeClass("bottom").addClass("top");
            }
        } else {
            $(this).parent().parent().find(".fancy-note-hover").removeClass("top").addClass("bottom");
        }
    });
    $(".headline .fancy-note-tooltip-hover").fancyNoteNextSiblingTooltip();
    $('.headline .tooltip').off('click').on('click', function (e) {
        e.preventDefault();
        if (hkexApp.utils.isTablet() || hkexApp.utils.isDesktop()) {
            return;
        }
        $(this).attr("data-target", "#headline-more").data('target', "#headline-more");
        var html = $(this).parent().parent().find('.fancy-note-hover').html();
        $("#headline-more .fancy-note-desc").html(html);
        $.fn.FancyPopup.call(this);
    });
}
table_tooltip();



function callFunctionAjaxRequest(data) {
    if (data.status == "success")
    setSelectedRadio(selectedDayPeriod); 
    load_js();
    
}

function load_js()
{
	(function ($) {
        $('table.table').tablesorter({
            sortMultiSortKey: null,
            numberSorter: function () {
                return 0;
        	},
        	textSorter: function () {
        	        return 0;
        	},
            onRenderHeader: function (index) {
                var _this = this;
                if (_this.hasClass('headline-toggle')) {
                    $('.hideHeadline').clone(true).appendTo(this);
                }
            }
        });
    })(jQuery);
	
	$("table.table.table-scroll").trigger("updateAll");
	
	
	
	$('table.table.table-scroll').scrollableTable({
	    tableWidth: 1000
	});
	table_tooltip();
	
	// Add Listener to Headline Show/Hide
	$('body').on('headlineHide headlineShow', function (e) {
	    var action = (e.type == 'headlineHide' ? "removeClass" : "addClass");
	    $('a.hideShow').filter(function () {
	        return $(this).children('.hide-headline').length > 0
	    })[action]("showing");
	});
	
	$('.PD_MB_filter_Btn').on('click', showFilter);
	$('.btn-close-filter').on('click', hideFilter);
	$('.filter__btn-applyFilters-js').on('click', hideFilter);
} 

function load_js_titleSearch()
{
	$('a.hideShow').on('click', toggleHeadline);
	load_js();
} 

function toggleHeadline() {
	$(this).toggleClass('showing');
    var action = $(this).hasClass('showing') ? 'removeClass' : 'addClass';
    var ViewDoc = $('body').hasClass("body-hidden");
    var eventName = "headlineHide";
    if (ViewDoc == true) {  
        $('body').removeClass("body-hidden");
        eventName = "headlineShow";
    }
    else {  
        $('body').addClass("body-hidden");
    }
    var ev = new $.Event(eventName);
    $('body').trigger(ev);
   
}

function showFilter() {
    $(document.body).addClass('showFilter');
    $(".PD_MB_filter").first().hide().toggle("slide");
}

function hideFilter() {
    $(".PD_MB_filter").first().toggle("slide");
    setTimeout(function () {
        $(document.body).removeClass('showFilter');
    }, 400);
    setTimeout(function () {
        $(".PD_MB_filter").first().show();
    },450);
}


