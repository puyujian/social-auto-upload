import json
import tempfile
import unittest
from pathlib import Path

import sau_cli
from uploader.xiaohongshu_uploader.protocol import build_image_note_payload, inject_long_article_trace, inject_topic_trace
from uploader.xiaohongshu_uploader.local_article_renderer import validate_input, render_article, BODY_BOTTOM
from PIL import Image


class LongArticleTests(unittest.TestCase):
    def test_input_ranges_preserve_unicode_and_reject_changed_text(self):
        data={'title':'标题','body':'甲😊乙\n丙丁','highlights':[{'from':1,'to':5,'text':'😊乙\n丙'}]}
        title,body,ranges=validate_input(data)
        self.assertEqual(ranges[0]['text'],'😊乙\n丙')
        data['highlights'][0]['text']='错误'
        with self.assertRaises(ValueError):validate_input(data)

    def test_local_pages_preserve_complete_source_and_use_valid_jpegs(self):
        body=('唱歌聚会先尊重大家的意愿，合理沟通时间与安排。\n\n'*30).strip()
        with tempfile.TemporaryDirectory() as directory:
            result=render_article({'title':'聚会如何安排','body':body},directory)
            layout=json.loads((Path(directory)/'layout.json').read_text(encoding='utf-8'))
            self.assertEqual(result['renderer'],'local_pillow')
            self.assertIsNone(result['album_id'])
            self.assertLessEqual(len(result['images']),18)
            lines=[line for page in layout['pages'] for line in page]
            self.assertEqual(''.join(line['text'] for line in lines),body.replace('\n',''))
            for line in lines:
                self.assertEqual(line['text'],body[line['from']:line['to']])
                self.assertLessEqual(line['y']+64,BODY_BOTTOM)
            for path in result['images']:
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual(image.size,(1440,1920))
                    self.assertEqual(image.format,'JPEG')

    def test_overflow_does_not_export_truncated_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'18张'):
                render_article({'title':'超页','body':'字'*10000},directory)
            self.assertFalse(list(Path(directory).glob('page-*.jpg')))

    def test_cli_exposes_generation_and_paired_publish_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'article.json'
            source.write_text('{}')
            args = sau_cli.build_parser().parse_args(['xiaohongshu', 'generate-article',
                '--account', 'test', '--input', str(source), '--output-dir', directory, '--headless'])
            self.assertEqual(args.action, 'generate-article')
            self.assertTrue(args.headless)

    def test_long_article_trace_matches_uploaded_page_order_and_preserves_topics(self):
        payload = build_image_note_payload(title='标题', description='正文',
            file_infos=[{'fileIds':'first'},{'fileIds':'second'}])
        inject_topic_trace(payload, {'id':'topic','name':'唱歌'})
        inject_long_article_trace(payload, 59, 750)
        context = json.loads(payload['common']['capa_trace_info']['contextJson'])
        self.assertEqual(context['longTextToImage'], {'albumId':59,
            'imageFileIds':['spectrum/first','spectrum/second'], 'wordCount':750})
        self.assertEqual(context['recommend_topics']['used'][0]['id'], 'topic')
        inject_topic_trace(payload, {'id':'another','name':'聚会'})
        self.assertIn('longTextToImage', json.loads(payload['common']['capa_trace_info']['contextJson']))


if __name__ == '__main__':
    unittest.main()
