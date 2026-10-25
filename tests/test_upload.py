import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from draft_fill import upload_image
from check_images import inspect
from PIL import Image
from playwright.sync_api import sync_playwright


class UploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.photo = str(Path(cls.tmp.name) / 'photo.jpg')
        Image.new('RGB', (1200, 900), 'red').save(cls.photo)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.tmp.cleanup()

    def page(self, fail=False):
        page = self.browser.new_page()
        page.set_content('''<button onclick="document.querySelector('input').click()">上传</button>
        <input type=file hidden><span id=count>0</span><div id=host></div>
        <script>document.querySelector('input').onchange = () => setTimeout(() => {
        document.querySelector('#host').innerHTML = '<div role="dialog"><canvas></canvas><button id="ok">确认</button></div>';
        document.querySelector('#ok').onclick = () => {
        document.querySelector('#host').innerHTML = '';
        REPLACE
        };}, 400);</script>'''.replace('REPLACE',
        "document.querySelector('#host').innerHTML = '<div role=alert>上传失败</div>';" if fail else
        "document.querySelector('#count').textContent = '1';"))
        return page

    def test_delayed_crop_confirmed(self):
        page = self.page()
        try:
            result = upload_image(page, [page.get_by_text('上传', exact=True)],
                [self.photo], '实物图', lambda: int(page.locator('#count').inner_text()), timeout=5)
            self.assertIn('预览已更新', result)
            self.assertEqual(page.locator('[role=dialog]').count(), 0)
        finally:
            page.close()

    def test_failed_upload_not_success(self):
        page = self.page(True)
        try:
            with self.assertRaisesRegex(RuntimeError, '上传失败'):
                upload_image(page, [page.get_by_text('上传', exact=True)], [self.photo],
                    '实物图', lambda: int(page.locator('#count').inner_text()), timeout=5)
        finally:
            page.close()

    def test_no_progress_times_out(self):
        page = self.browser.new_page()
        page.set_content('<input type=file>')
        try:
            with self.assertRaisesRegex(RuntimeError, '未确认上传完成'):
                upload_image(page, [page.locator('input')], [self.photo],
                    '封面', lambda: [], timeout=.5)
        finally:
            page.close()

    def test_image_validation(self):
        self.assertEqual(inspect(self.photo)['width'], 1200)
        bad = Path(self.tmp.name) / 'bad.jpg'
        bad.write_bytes(b'not an image')
        with self.assertRaises(Exception):
            inspect(bad)


if __name__ == '__main__':
    unittest.main()
