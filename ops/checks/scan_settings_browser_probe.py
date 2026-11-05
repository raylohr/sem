"""Actual Chromium regression: accepts rendered scan card and static assets as JSON on stdin.
Runs entirely with synthetic HTML; never submits a crawl or contacts a website.
"""
import json
import sys
from playwright.sync_api import sync_playwright

payload=json.load(sys.stdin)
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,chromium_sandbox=True)
    for name,code in [('before',payload['before']),('after',payload['after'])]:
        page=browser.new_page(viewport={'width':1440,'height':900})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/*',lambda route:route.abort())
        page.set_content('<html><head><style>'+payload['css']+'</style></head><body>'+payload['html']+'</body></html>')
        page.add_script_tag(content=payload['bootstrap'])
        page.add_script_tag(content=code)
        page.locator('[data-bs-toggle="modal"]').click()
        editor=page.locator('form[data-scan-config]')
        page.locator('.modal.show').wait_for()
        if name=='before':
            assert errors and any('multiple' in error for error in errors),errors
            page.close();continue
        assert not errors,errors
        assert editor.evaluate('(form)=>form.checkValidity()')
        assert editor.locator('[name="resource_max_bytes"]').input_value()=='262144000'
        editor.locator('[name="max_urls"]').fill('1234')
        editor.locator('[name="read_timeout"]').fill('90')
        editor.locator('details').evaluate_all('(nodes)=>nodes.forEach(node=>node.open=true)')
        editor.locator('[name="browser_max_pages"]').fill('10')
        editor.locator('[name="schema_profiles"]').select_option(['google-product-snippet-v1'])
        editor.locator('button[type="submit"]').click()
        page.locator('.modal.show').wait_for(state='hidden')
        values=page.locator('form[data-api-form]').evaluate('(f)=>Object.fromEntries(new FormData(f).entries())')
        assert values['max_urls']=='1234' and values['read_timeout']=='90' and values['browser_max_pages']=='10',values
        assert values['resource_max_bytes']=='262144000' and values['check_external_links']=='true',values
        assert values['schema_profiles']=='google-product-snippet-v1',values
        assert 'Instellingen opgeslagen' in page.locator('[data-scan-config-message]').inner_text()
        page.locator('[data-bs-toggle="modal"]').click()
        page.locator('.modal.show').wait_for()
        assert editor.locator('[name="max_urls"]').input_value()=='1.234'
        editor.locator('[name="max_urls"]').fill('2222')
        editor.get_by_role('button',name='Annuleren').click()
        page.locator('.modal.show').wait_for(state='hidden')
        assert page.locator('form[data-api-form] [name="max_urls"]').input_value()=='1234'
        assert not errors,errors
        page.close()
    browser.close()
print('PASS: original missing-field error reproduced; corrected modal opens, saves numeric budgets and multiselect, restores saved settings and cancels edits without starting a scan.')
