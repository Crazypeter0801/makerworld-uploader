"""Standard-library checks for preflight.py (no browser needed)."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import preflight  # noqa: E402

MODEL = ('<model><resources><object id="1"><mesh><vertices>'
         '<vertex x="0" y="0" z="0"/><vertex x="100" y="50" z="10"/>'
         '</vertices></mesh></object></resources><build><item objectid="1"/></build></model>')


def make_3mf(folder, name, types, presets, layer=0.2, first=0.2, plate_layer=None):
    path = Path(folder) / name
    settings = dict(filament_type=types, filament_settings_id=presets,
                    nozzle_temperature=['220'] * len(types), layer_height=str(layer),
                    initial_layer_print_height=str(first), printer_model='Bambu Lab P2S')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('3D/3dmodel.model', MODEL)
        z.writestr('Metadata/project_settings.config', json.dumps(settings))
        if plate_layer is not None:
            z.writestr('Metadata/plate_1.json', json.dumps(dict(
                bbox_all=[50, 50, 150, 100], bbox_objects=[dict(name='a', layer_height=plate_layer)])))
    return str(path)


class Preflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pla_with_petg_cf_is_not_high_low_mix(self):
        f = make_3mf(self.tmp.name, 'a.3mf', ['PLA', 'PETG-CF'],
                     ['Bambu PLA Basic @BBL P2S', 'Bambu PETG-CF @BBL P2S 0.4 nozzle'])
        self.assertEqual(preflight.check(f)['blockers'], [])

    def test_pla_with_abs_is_blocked(self):
        f = make_3mf(self.tmp.name, 'b.3mf', ['PLA', 'ABS'],
                     ['Bambu PLA Basic @BBL P2S', 'Bambu ABS @BBL P2S'])
        self.assertTrue(any('高低温' in b for b in preflight.check(f)['blockers']))

    def test_generic_system_preset_is_not_custom(self):
        f = make_3mf(self.tmp.name, 'c.3mf', ['PLA'], ['Generic PLA @BBL P2S'])
        self.assertFalse(any('自定义' in w for w in preflight.check(f)['warnings']))
        g = make_3mf(self.tmp.name, 'd.3mf', ['PLA'], ['My PLA'])
        self.assertTrue(any('自定义' in w for w in preflight.check(g)['warnings']))

    def test_single_digit_layer_in_filename(self):
        f = make_3mf(self.tmp.name, 'tray_petg_0.2.3mf', ['PETG'], ['Bambu PETG HF @BBL P2S'], layer=0.12)
        self.assertTrue(any('文件名标的是 0.2' in w for w in preflight.check(f)['warnings']))

    def test_plate_json_records_first_layer(self):
        f = make_3mf(self.tmp.name, 'e.3mf', ['PLA'], ['Bambu PLA Basic @BBL P2S'],
                     layer=0.12, first=0.2, plate_layer=0.2)
        self.assertFalse(any('plate_1.json' in w for w in preflight.check(f)['warnings']))


if __name__ == '__main__':
    unittest.main()
