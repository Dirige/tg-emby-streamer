"""Dashboard behavior tests; QuickJS is an optional, test-only dependency."""
import json
import re
from pathlib import Path

import pytest

quickjs = pytest.importorskip("quickjs")
HTML = (Path(__file__).parents[1] / "app/static/index.html").read_text()
SCRIPT = re.search(r"<script>(.*?)</script>", HTML, re.S).group(1).split("const initPage =")[0]

DOM = r"""
class FakeOption {
  constructor(text, value) { this.text = String(text); this.value = String(value); }
}
class FakeElement {
  constructor() {
    this.value = ''; this.checked = false; this.style = {}; this._html = ''; this._text = null;
    const names = new Set();
    this.classList = {add: name => names.add(name), remove: name => names.delete(name), contains: name => names.has(name)};
  }
  set innerHTML(value) { this._html = String(value); this._text = null; }
  get innerHTML() { return this._text === null ? this._html : this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  set textContent(value) { this._text = String(value); }
  get textContent() { return this._text || ''; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  appendChild() {}
  remove() {}
}
class FakeSelect extends FakeElement {
  constructor(values = []) {
    super();
    this.options = values.map(value => new FakeOption(value, value));
    this._value = this.options[0]?.value || '';
  }
  get value() { return this._value || ''; }
  set value(value) {
    value = String(value);
    this._value = this.options?.some(option => option.value === value) ? value : '';
  }
  replaceChildren(...options) { this.options = options; this._value = options[0]?.value || ''; }
  add(option) { this.options.push(option); if (this.options.length === 1) this._value = option.value; }
}
globalThis.Option = FakeOption;
globalThis.elements = Object.fromEntries(htmlIds.map(id => [id, new FakeElement()]));
elements.ruleCategory = new FakeSelect(['']);
elements.mediaFilter = new FakeSelect(['']);
elements.mediaEditCategory = new FakeSelect();
elements.mediaPageSize = new FakeSelect(['20', '50', '100']);
elements.mediaSearchField = new FakeSelect(['title', 'file_name', 'channel', 'all']);
globalThis.document = {
  getElementById: id => elements[id] || null,
  querySelectorAll: () => [], querySelector: () => null,
  createElement: () => new FakeElement(), body: new FakeElement(),
};
globalThis.timers = new Map();
let nextTimer = 1;
globalThis.setTimeout = (fn, delay) => { const id = nextTimer++; timers.set(id, {fn, delay}); return id; };
globalThis.clearTimeout = id => timers.delete(id);
globalThis.URLSearchParams = class {
  constructor(values) { this.values = values; }
  toString() {
    const encode = value => encodeURIComponent(String(value)).replace(/%20/g, '+');
    return Object.entries(this.values).map(([key, value]) => encode(key) + '=' + encode(value)).join('&');
  }
};
globalThis.console = {warn() {}, error() {}};
globalThis.confirm = () => true;
"""

MOCKS = """
globalThis.calls = []; globalThis.posts = []; globalThis.messages = [];
globalThis.responses = {
  categories: {categories: []}, rules: {rules: []}, rule: {rule: null},
  media: {items: [], total: 0}, detail: {item: null},
};
globalThis.rendered = []; globalThis.pagination = null;
globalThis.realRenderMedia = renderMedia;
renderMedia = items => { rendered = items; };
renderPagination = (...args) => { pagination = args.slice(0, 4); };
toast = (message, type) => messages.push({message, type});
populateScanDropdown = async () => {};
api = async path => {
  calls.push(path);
  if (path === '/api/categories') return responses.categories;
  if (path === '/api/channel-rules') return responses.rules;
  if (path.startsWith('/api/channel-rule?')) return responses.rule;
  if (path.startsWith('/api/media?')) return responses.media;
  if (path.startsWith('/api/media/')) return responses.detail;
  throw new Error('Unexpected API request: ' + path);
};
apiPost = async (path, body) => {
  posts.push({path, body});
  if (path === '/api/category/save') responses.categories.categories.push({id: 42, name: body.name});
  return {success: true};
};
"""


@pytest.fixture
def browser():
    context = quickjs.Context()
    context.set_memory_limit(32 * 1024 * 1024)
    context.eval("globalThis.htmlIds = " + json.dumps(re.findall(r'id="([^"]+)"', HTML)) + ";")
    context.eval(DOM)
    context.eval(SCRIPT)
    context.eval(MOCKS)
    return context


def run(browser, code):
    browser.eval("globalThis.done = false; globalThis.failure = null; globalThis.result = null;")
    browser.eval("(async () => {" + code + "})().then(value => {result = value; done = true;}, error => {failure = String(error.stack || error); done = true;});")
    for _ in range(1000):
        if browser.eval("done"):
            break
        if not browser.execute_pending_job():
            break
    assert browser.eval("done"), "JavaScript test did not settle"
    assert browser.eval("failure") is None, browser.eval("failure")
    value = browser.eval("JSON.stringify(result)")
    return json.loads(value) if value else None


def test_categories_populate_all_three_selects_without_html_injection(browser):
    result = run(browser, """
      await populateCategoryFilters([{name: '水果派'}, {name: '国产'}, {name: 'AI短剧'}, {name: '无码'}, {name: '分类"<特殊>&'}]);
      return ['ruleCategory', 'mediaFilter', 'mediaEditCategory'].map(id => elements[id].options.map(option => option.value));
    """)
    for options in result:
        assert {"水果派", "国产", "AI短剧", "无码", '分类"<特殊>&'} <= set(options)
        assert len(options) == len(set(options))
    assert result[1][0] == ""


def test_add_channel_fetches_new_categories_and_saves_selected_value(browser):
    result = run(browser, """
      responses.categories = {categories: [{name: '水果派'}]};
      await showAddChannelModal();
      elements.ruleCategory.value = '水果派';
      elements.ruleChannelId.value = '-1006';
      await saveRule();
      return {shown: elements.ruleModal.classList.contains('show'), posts, calls};
    """)
    assert result["posts"][0]["body"]["category"] == "水果派"
    assert result["posts"][0]["path"] == "/api/channel-rule/save"
    assert "/api/categories" in result["calls"]


def test_edit_channel_preserves_a_custom_or_legacy_category(browser):
    result = run(browser, """
      responses.rule = {rule: {category: '旧分类', channel_name: '现有频道', enabled: false}};
      await openRuleEditor('-1006');
      await saveRule();
      return {options: elements.ruleCategory.options.map(option => option.value), posts};
    """)
    assert "旧分类" in result["options"]
    assert result["posts"][0]["body"]["category"] == "旧分类"
    assert result["posts"][0]["body"]["enabled"] is False


def test_saving_a_category_refreshes_channel_and_media_options_immediately(browser):
    result = run(browser, """
      elements.categoryName.value = '新增分类';
      elements.categorySortOrder.value = '8';
      await saveCategory();
      return ['ruleCategory', 'mediaFilter', 'mediaEditCategory'].map(id => elements[id].options.map(option => option.value));
    """)
    assert all("新增分类" in options for options in result)


def test_category_refresh_keeps_current_media_filter_and_edit_value(browser):
    result = run(browser, """
      await populateCategoryFilters([{name: '国产'}]);
      elements.mediaFilter.value = '国产';
      elements.mediaEditCategory.value = '18+';
      await populateCategoryFilters([{name: '国产'}, {name: '无码'}]);
      return {filter: elements.mediaFilter.value, edit: elements.mediaEditCategory.value};
    """)
    assert result == {"filter": "国产", "edit": "18+"}


def test_failed_category_load_does_not_open_an_unusable_channel_form(browser):
    result = run(browser, """
      responses.categories = null;
      await showAddChannelModal();
      return {shown: elements.ruleModal.classList.contains('show'), messages};
    """)
    assert result["shown"] is False
    assert result["messages"][0]["type"] == "error"


def test_search_encodes_special_characters_and_preserves_pagination(browser):
    from urllib.parse import parse_qs, urlsplit

    query = run(browser, """
      await populateCategoryFilters([{name: '国产 & +'}]);
      elements.mediaFilter.value = '国产 & +';
      elements.mediaSearch.value = '  中文 & + .mp4  ';
      elements.mediaPageSize.value = '50';
      mediaPage = 7;
      await loadMedia();
      return calls[calls.length - 1];
    """)
    params = parse_qs(urlsplit(query).query)
    assert params == {"page": ["7"], "page_size": ["50"], "category": ["国产 & +"], "search": ["中文 & + .mp4"], "search_field": ["title"]}


def test_enter_search_resets_page_and_cancels_pending_debounce(browser):
    result = run(browser, """
      mediaPage = 8;
      scheduleMediaSearch();
      let prevented = false;
      await searchMedia({preventDefault() {prevented = true;}});
      return {prevented, page: mediaPage, timerCount: timers.size, calls};
    """)
    assert result["prevented"] and result["page"] == 1
    assert result["timerCount"] == 0
    assert "page=1" in result["calls"][-1]


def test_clear_search_keeps_category_and_search_scope(browser):
    result = run(browser, """
      await populateCategoryFilters([{name: '国产'}]);
      elements.mediaFilter.value = '国产';
      elements.mediaSearchField.value = 'channel';
      elements.mediaSearch.value = '-1001';
      mediaPage = 9;
      await clearMediaSearch();
      return {query: elements.mediaSearch.value, category: elements.mediaFilter.value, scope: elements.mediaSearchField.value, page: mediaPage};
    """)
    assert result == {"query": "", "category": "国产", "scope": "channel", "page": 1}


def test_typing_debounces_to_only_the_latest_search(browser):
    result = run(browser, """
      elements.mediaSearch.value = 'old';
      scheduleMediaSearch();
      elements.mediaSearch.value = 'SONE-436';
      scheduleMediaSearch();
      const pending = [...timers.values()];
      await pending[0].fn();
      return {pendingCount: pending.length, delay: pending[0].delay, calls};
    """)
    assert result["pendingCount"] == 1 and result["delay"] == 300
    assert len(result["calls"]) == 1 and "search=SONE-436" in result["calls"][0]


def test_slow_old_response_cannot_replace_new_search_results(browser):
    result = run(browser, """
      let finishOld;
      let count = 0;
      api = path => ++count === 1 ? new Promise(resolve => {finishOld = resolve;}) : Promise.resolve({items: [{id: 2}], total: 1});
      elements.mediaSearch.value = 'old';
      const oldRequest = loadMedia();
      elements.mediaSearch.value = 'new';
      await loadMedia();
      finishOld({items: [{id: 1}], total: 20});
      await oldRequest;
      return {rendered, total: mediaTotal};
    """)
    assert result == {"rendered": [{"id": 2}], "total": 1}


def test_no_matches_has_a_clear_search_message(browser):
    result = run(browser, """
      elements.mediaSearch.value = '不存在';
      realRenderMedia([]);
      return elements.mediaTable.innerHTML;
    """)
    assert "没有找到匹配的媒体" in result


def test_new_channel_form_enables_and_saves_safety_option(browser):
    result = run(browser, """
      await showAddChannelModal();
      elements.ruleChannelId.value = '-1006';
      await saveRule();
      return posts[0].body.strict_import;
    """)
    assert result is True


def test_editing_legacy_channel_does_not_silently_enable_safety_option(browser):
    result = run(browser, """
      responses.rule = {rule: {category: '18+', enabled: true}};
      await openRuleEditor('-1006');
      await saveRule();
      return posts[0].body.strict_import;
    """)
    assert result is False


def test_editing_verified_channel_preserves_enabled_safety_option(browser):
    result = run(browser, """
      responses.rule = {rule: {category: '国产', enabled: true, strict_import: true}};
      await openRuleEditor('-1006');
      await saveRule();
      return posts[0].body.strict_import;
    """)
    assert result is True
