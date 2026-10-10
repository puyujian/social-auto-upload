"""Run pinned Xiaohongshu template components, fonts and WASM offline."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.async_api import async_playwright

from uploader.xiaohongshu_uploader.local_article_renderer import validate_input

ALBUM_TO_THEME = {6:5,2:0,5:4,1:1,4:2,3:3,56:8,62:62,60:60,61:61,55:55,
    48:48,49:49,50:50,51:51,52:52,53:53,54:7,57:10,7:6,59:59}


def assets_root():
    configured=os.environ.get('XHS_ORIGINAL_ARTICLE_ASSETS','')
    root=Path(configured or '/app/data/xhs-original-article-assets').resolve()
    if not (root/'1426.html').is_file():
        raise RuntimeError('小红书原始排版资源未安装：'+str(root))
    return root


def text_content(node):
    return str(node.get('text') or '')+''.join(text_content(n) for n in node.get('content',[]))


async def render_original_article(data,output_dir):
    started=time.monotonic()
    title,body,highlights=validate_input(data)
    root=assets_root()
    catalog=json.loads((root/'official-template-catalog.json').read_text(encoding='utf-8'))['data']['resources']
    template_id=data.get('template_id') or 6
    template=next((t for t in catalog if t['id']==template_id),None)
    if template is None:raise ValueError('未知的小红书原始模板')
    color=data.get('template_color') or template['colors'][0]['main_color']
    palette=next((c for c in template['colors'] if c['main_color'].lower()==color.lower()),None)
    if palette is None:raise ValueError('配色不属于所选原始模板')
    params={'title':title,'themeId':ALBUM_TO_THEME[template_id],
        'colors':json.loads(palette['color_map']),'wordCount':len(body),
        'summary':re.split(r'(?<=[。！？])',body.strip())[0][:100]}
    font_manifest=json.loads((root/'font-manifest.json').read_text(encoding='utf-8'))
    assets=json.loads((root/'asset-manifest.json').read_text(encoding='utf-8')) if (root/'asset-manifest.json').is_file() else []
    asset_map={a['url']:a for a in assets}
    output_dir=Path(output_dir).resolve();output_dir.mkdir(parents=True,exist_ok=True)
    pending={'type':'doc','content':[]}
    source_offset=0
    for i,line in enumerate(body.split('\n')):
        leaves=[];cursor=0
        for span in highlights:
            left=max(0,span['from']-source_offset);right=min(len(line),span['to']-source_offset)
            if left<right:
                if cursor<left:leaves.append({'type':'text','text':line[cursor:left]})
                leaves.append({'type':'text','text':line[left:right],'marks':[{'type':'highlight'}]})
                cursor=right
        if cursor<len(line):leaves.append({'type':'text','text':line[cursor:]})
        pending['content'].append({'type':'paragraph','attrs':{'textAlign':None,'uuid':str(i),
            'hasSplit':False,'charPositionInPara':0,'textLength':0,'left':False},
            'content':leaves})
        source_offset+=len(line)+1
    pages=[]
    requests=[]
    async with async_playwright() as playwright:
        executable=os.environ.get('XHS_LOCAL_RENDERER_BROWSER','/usr/bin/google-chrome')
        browser=await playwright.chromium.launch(executable_path=executable,headless=True,
            args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
        try:
            page=await browser.new_page(viewport={'width':900,'height':1500},device_scale_factor=1.6)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            async def route(request):
                url=request.request.url
                requests.append(url)
                asset=asset_map.get(url)
                path=(root/asset['path']).resolve() if asset else (root/unquote(urlsplit(url).path).lstrip('/')).resolve()
                if (asset or urlsplit(url).hostname=='xhs.local') and path.is_relative_to(root) and path.is_file():
                    await request.fulfill(path=path,content_type=asset['mime'] if asset else 'application/wasm' if path.suffix=='.wasm' else 'application/octet-stream' if path.suffix=='.font' else None)
                else:
                    await request.abort()
            await page.route('**/*',route)
            await page.goto('http://xhs.local/1426.html')
            await page.evaluate("xhsRequire.p='http://xhs.local/'")
            await page.evaluate(r"""manifest=>{const style=document.createElement('style');style.textContent=manifest.map(f=>`@font-face {font-family:'${f.name}';src:url('/${f.path}');font-weight:${f.weight};font-display:block;}`).join('\n')+'body{margin:0;--emoji:\"emoji-ios\";}';document.head.appendChild(style);} """,font_manifest)
            await page.evaluate('async()=>await xhsRequire(12077).xZ.initialize()')
            separate_cover=await page.evaluate('id=>hasSeparateCover(id)',params['themeId'])
            if not separate_cover:
                title_nodes=await page.evaluate('d=>originalTitleNodes(d.themeId,d.title)',params)
                pending['content']=title_nodes+pending['content']
            queue=[pending]
            iterations=0
            trace=[]
            original_completed=set()
            while queue:
                pending=queue.pop(0)
                if not pending.get('content'):continue
                iterations+=1
                if iterations>100:raise RuntimeError('原始分页未收敛')
                rendered=await page.evaluate('async d=>await renderBody(d.document,d.params)',{'document':pending,'params':params})
                trace.append({'iteration':iterations,'chars':len(text_content(pending)),'overflow':rendered['overflow'],'geometry':rendered['geometry']})
                (output_dir/'pagination-trace.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
                if not rendered['overflow']['isOverflowing']:
                    pages.append(pending);continue
                result=await page.evaluate("""async d=>{const result=await xhsRequire(12077).xZ.splitPage(JSON.stringify(d.doc),JSON.stringify(d.layouts),JSON.stringify(d.geometry));return {left:JSON.parse(result.left.toString()),right:JSON.parse(result.right.toString())};}""",{'doc':pending,'layouts':rendered['layouts'],'geometry':rendered['geometry']})
                if (not text_content(result['right']) and text_content(result['left'])==text_content(pending)
                    and rendered['overflow']['lastElementBottom']<=rendered['geometry']['height']):
                    # Decorative paper padding can enter the reserved margin.
                    # Keep the original engine's completed schema when the
                    # entire rendered page still fits the physical canvas.
                    pages.append(pending)
                    original_completed.add(json.dumps(pending,sort_keys=True))
                    continue
                if not text_content(result['left']) or text_content(result['right'])==text_content(pending):
                    raise RuntimeError('原始分页组件未能推进分页')
                queue[0:0]=[result['left'],result['right']]
                if len(pages)+(1 if separate_cover else 0)>=18:raise ValueError('长文最多18张，请缩短正文')
            if len(pages)+(1 if separate_cover else 0)>18:raise ValueError('长文最多18张，请缩短正文')
            compact=lambda s:re.sub(r'\s+','',s)
            if compact(''.join(text_content(p) for p in pages))!=compact(body):
                raise RuntimeError('原始分页后的正文不完整')
            files=[]
            if separate_cover:
                cover=await page.evaluate('async d=>await renderCover(d)',params)
                if compact(title) not in compact(cover['text']):raise RuntimeError('原始封面未显示完整标题')
                path=output_dir/'page-01.jpg';await page.screenshot(path=str(path),type='jpeg',quality=94);files.append(str(path))
            for i,doc in enumerate(pages,2 if separate_cover else 1):
                rendered=await page.evaluate('async d=>await renderBody(d.document,d.params)',{'document':doc,'params':params})
                if rendered['overflow']['isOverflowing'] and json.dumps(doc,sort_keys=True) not in original_completed:
                    raise RuntimeError('原始分页结果仍超出页面')
                path=output_dir/f'page-{i:02}.jpg';await page.screenshot(path=str(path),type='jpeg',quality=94);files.append(str(path))
            if errors:raise RuntimeError('原始模板运行错误：'+errors[0])
            loaded_fonts=await page.evaluate("()=>[...document.fonts].filter(f=>f.status==='loaded').map(f=>({family:f.family,weight:f.weight}))")
        finally:
            await browser.close()
    result={'renderer':'xhs_original_local','renderer_version':1,'template_id':template_id,
        'theme_id':params['themeId'],'template_name':template['name'],'template_color':color,
        'album_id':None,'word_count':len(body),'images':files,'dimensions':[[1440,2400] for _ in files],
        'loaded_fonts':loaded_fonts,'elapsed_seconds':round(time.monotonic()-started,3)}
    (output_dir/'layout.json').write_text(json.dumps({'renderer':result['renderer'],'source_content':body,
        'highlights':highlights,'schema_pages':pages,'template_id':template_id,'template_color':color},ensure_ascii=False,indent=2),encoding='utf-8')
    (output_dir/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
