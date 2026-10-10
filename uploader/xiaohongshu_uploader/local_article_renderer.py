"""Bounded-memory long-article pagination and JPEG rendering with Pillow."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1440, 1920
MARGIN, BODY_SIZE, LINE_HEIGHT = 120, 64, 92
BODY_TOP, BODY_BOTTOM = 310, 1745
CLOSING = set('，。！？；：、）】》〉」』”’％%')
OPENING = set('（【《〈「『“‘')
BACKGROUND, INK, MUTED = '#F7F4ED', '#262A2C', '#77766F'


def validate_input(data):
    title, body = data.get('title'), data.get('body')
    if not isinstance(title,str) or not title.strip() or len(title)>64:
        raise ValueError('长文标题须为1至64字')
    if not isinstance(body,str) or not body.strip() or len(body)>10000:
        raise ValueError('长文正文须为1至10000字')
    ranges = data.get('highlights',[])
    if not isinstance(ranges,list):
        raise ValueError('高亮选区须为列表')
    end = 0
    for item in ranges:
        if (not isinstance(item,dict) or type(item.get('from')) is not int or
            type(item.get('to')) is not int or not end<=item['from']<item['to']<=len(body) or
            body[item['from']:item['to']] != item.get('text')):
            raise ValueError('高亮选区越界、重叠或与正文不一致')
        end = item['to']
    return title.strip(),body,ranges


def font_path():
    for candidate in [os.environ.get('XHS_LONG_ARTICLE_FONT',''),
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        'C:/Windows/Fonts/msyh.ttc']:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError('本地长文排版缺少中文字体，请安装fonts-wqy-zenhei或配置XHS_LONG_ARTICLE_FONT')


def wrap_text(text,font,width,*,start=0):
    """Preserve Unicode positions and keep punctuation with its neighboring text."""
    lengths={char:font.getlength(char) for char in set(text)}
    result,offset=[],0
    while offset<len(text):
        end,used=offset,0.0
        while end<len(text) and used+lengths[text[end]]<=width:
            used+=lengths[text[end]]
            end+=1
        if end==offset:
            raise ValueError('字体字符宽度超过页面内容宽度')
        while end<len(text) and text[end] in CLOSING and end-offset>1:
            end-=1
        while end-offset>1 and text[end-1] in OPENING:
            end-=1
        result.append({'text':text[offset:end],'from':start+offset,'to':start+end})
        offset=end
    return result


def paginate(body,font):
    pages,lines,y,offset=[],[],BODY_TOP,0
    for paragraph in body.split('\n'):
        if not paragraph:
            y+=28
        else:
            for line in wrap_text(paragraph,font,WIDTH-2*MARGIN,start=offset):
                if y+BODY_SIZE>BODY_BOTTOM:
                    pages.append(lines)
                    lines,y=[],BODY_TOP
                lines.append({**line,'x':MARGIN,'y':y})
                y+=LINE_HEIGHT
            y+=22
        offset+=len(paragraph)+1
    if lines:
        pages.append(lines)
    if len(pages)>1 and len(pages[-1])<5:
        tail=pages[-2]+pages[-1]
        split=(len(tail)+1)//2
        balanced=[]
        for group in (tail[:split],tail[split:]):
            new_lines,y,previous=[],BODY_TOP,None
            for line in group:
                if previous is not None and line['from']>previous['to']:
                    y+=22
                new_lines.append({**line,'y':y})
                y+=LINE_HEIGHT
                previous=line
            balanced.append(new_lines)
        pages[-2:]=balanced
    if not pages or len(pages)+1>18:
        raise ValueError('本地长文含封面最多18张，请缩短正文')
    return pages


def draw_line(draw,line,font,highlights=()):
    x,y=line['x'],line['y']
    for index,char in enumerate(line['text'],line['from']):
        width=font.getlength(char)
        if any(span['from']<=index<span['to'] for span in highlights):
            draw.rounded_rectangle((x-2,y-4,x+width+2,y+font.size+8),radius=5,fill='#F1D987')
        draw.text((x,y+font.getmetrics()[0]),char,font=font,fill=INK,anchor='ls')
        x+=width


def render_article(data,output_dir):
    started=time.monotonic()
    title,body,highlights=validate_input(data)
    output_dir=Path(output_dir).resolve()
    output_dir.mkdir(parents=True,exist_ok=True)
    path=font_path()
    fonts={size:ImageFont.truetype(path,size) for size in (32,38,56,64,92)}
    pages=paginate(body,fonts[BODY_SIZE])
    total=len(pages)+1
    filenames=[]
    cover_lines=wrap_text(title,fonts[92],WIDTH-2*MARGIN)
    if len(cover_lines)>5:
        raise ValueError('标题在本地封面中超过5行，请缩短标题')
    excerpt=next(p for p in body.split('\n') if p.strip())
    excerpt=excerpt[:100]+('…' if len(excerpt)>100 else '')
    for page_index in range(total):
        image=Image.new('RGB',(WIDTH,HEIGHT),BACKGROUND)
        draw=ImageDraw.Draw(image)
        if page_index==0:
            draw.text((MARGIN,180),'阅读笔记',font=fonts[38],fill=MUTED,anchor='lt')
            draw.rounded_rectangle((MARGIN,280,MARGIN+90,294),radius=7,fill='#D6B365')
            y=440
            for line in cover_lines:
                draw_line(draw,{**line,'x':MARGIN,'y':y},fonts[92])
                y+=126
            y=max(970,y+100)
            for line in wrap_text(excerpt,fonts[56],WIDTH-2*MARGIN)[:6]:
                draw_line(draw,{**line,'x':MARGIN,'y':y},fonts[56])
                y+=82
        else:
            header=title if len(title)<=24 else title[:24]+'…'
            draw.text((MARGIN,105),header,font=fonts[38],fill=MUTED,anchor='lt')
            draw.line((MARGIN,195,WIDTH-MARGIN,195),fill='#DAD6CC',width=2)
            for line in pages[page_index-1]:
                draw_line(draw,line,fonts[BODY_SIZE],highlights)
        draw.text((WIDTH-MARGIN,1820),f'{page_index+1:02} / {total:02}',font=fonts[32],fill=MUTED,anchor='rt')
        destination=output_dir/f'page-{page_index+1:02}.jpg'
        image.save(destination,'JPEG',quality=94,optimize=True)
        image.close()
        filenames.append(str(destination))
    layout={'renderer':'local_pillow','renderer_version':1,'size':[WIDTH,HEIGHT],
        'font':Path(path).name,'title':title,'source_content':body,'highlights':highlights,'pages':pages}
    (output_dir/'layout.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2),encoding='utf-8')
    result={'renderer':'local_pillow','renderer_version':1,'album_id':None,'word_count':len(body),
        'images':filenames,'dimensions':[[WIDTH,HEIGHT] for _ in filenames],
        'elapsed_seconds':round(time.monotonic()-started,3)}
    (output_dir/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
