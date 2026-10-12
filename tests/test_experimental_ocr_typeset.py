"""Small geometry/layout regressions, without models or chapter inference."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "src"))
from manga_pipeline.v6.config import DEFAULT_CONFIG
from manga_pipeline.v6.experimental_ocr_typeset import area_allocation, fit_with_expansion, grow_regions, ocr_regions, rectangle_gap
from manga_pipeline.v6.typeset import resolve_font


def region(identifier,bounds,allowed,area=None):
    rectangle = np.zeros_like(allowed)
    x1,y1,x2,y2 = bounds
    rectangle[y1:y2,x1:x2] = True
    mask = rectangle & allowed
    return {"id":identifier,"bbox":list(bounds),"mask":mask,"body_mask_array":rectangle,
        "allowed_mask_array":allowed,"anchor":[(x1+x2)/2,(y1+y2)/2],
        "usable_area_px":int(mask.sum()),"allocation_area_px":area or int(mask.sum()),
        "blocked_directions":{},"area_share":1}


class ExperimentalOCRTypesetTests(unittest.TestCase):
    def test_character_quota_and_token_order(self):
        allowed = np.ones((100,300),dtype=bool)
        regions = [region("a",[0,0,90,100],allowed,100),region("b",[100,0,290,100],allowed,200)]
        text = "one two six ten sun sky red sea bee"
        plan = area_allocation(text,regions)
        self.assertEqual(plan["parts"][0]["word_range"],[0,3])
        self.assertEqual(plan["parts"][1]["word_range"],[3,9])
        self.assertEqual(" ".join(p["text"] for p in plan["parts"]),text)
        unequal = area_allocation("a abcdefgh b c defghijk",regions)
        self.assertEqual(sum(p["allocated_characters"] for p in unequal["parts"]),19)

    def test_blocked_direction_does_not_stop_other_directions(self):
        allowed = np.zeros((120,180),dtype=bool)
        allowed[10:110,10:170] = True
        regions = [region("a",[10,40,70,70],allowed)]
        changes = grow_regions(regions,{"a"},step_px=2,gap_px=10)
        self.assertEqual(regions[0]["bbox"][0],10)
        self.assertEqual(regions[0]["blocked_directions"]["left"],"body_margin_boundary")
        self.assertEqual({c["direction"] for c in changes},{"right","up","down"})
        self.assertFalse(np.any(regions[0]["mask"] & ~allowed))

    def test_neighbor_gap_is_protected(self):
        allowed = np.ones((100,200),dtype=bool)
        regions = [region("a",[20,20,80,80],allowed),region("b",[90,20,150,80],allowed)]
        grow_regions(regions,{"a","b"},step_px=2,gap_px=10)
        self.assertEqual(regions[0]["blocked_directions"]["right"],"neighbor_gap")
        self.assertEqual(regions[1]["blocked_directions"]["left"],"neighbor_gap")
        self.assertGreaterEqual(rectangle_gap(regions[0]["bbox"],regions[1]["bbox"]),10)
        self.assertFalse(np.any(regions[0]["mask"] & regions[1]["mask"]))

    def test_shared_font_and_fixed_quota(self):
        allowed = np.zeros((160,300),dtype=bool)
        allowed[10:150,10:290] = True
        regions = [region("a",[20,30,110,120],allowed),region("b",[130,30,280,120],allowed)]
        trials = []
        text = "one two three four five six seven eight nine ten"
        plan,glyphs,final,growth = fit_with_expansion(text,regions,resolve_font(DEFAULT_CONFIG),DEFAULT_CONFIG,trace=trials.append)
        self.assertIsNotNone(plan)
        self.assertEqual(len(glyphs),2)
        self.assertFalse(growth)
        for glyph,r in zip(glyphs,final):
            self.assertFalse(np.any((np.asarray(glyph)>0) & ~r["mask"]))
        ranges = [p["word_range"] for p in plan["parts"]]
        self.assertTrue(all([p["word_range"] for p in trial["parts"]] == ranges for trial in trials))

    def test_overflow_expands_and_fits_inside_safe_body(self):
        allowed = np.zeros((180,220),dtype=bool)
        allowed[10:170,10:210] = True
        regions = [region("a",[30,50,75,85],allowed)]
        config = {**DEFAULT_CONFIG,"font_min_px":24,"font_max_px":24}
        plan,glyphs,final,growth = fit_with_expansion("one two three four five six",regions,resolve_font(config),config)
        self.assertIsNotNone(plan)
        self.assertTrue(growth)
        self.assertFalse(np.any((np.asarray(glyphs[0])>0) & ~allowed))
        self.assertEqual(plan["allocation"]["parts"][0]["allocation_area_px"],45*35)

    def test_nearby_regions_cannot_expand_even_on_overflow(self):
        allowed = np.ones((100,130),dtype=bool)
        regions = [region("a",[20,30,50,50],allowed),region("b",[54,30,84,50],allowed)]
        config = {**DEFAULT_CONFIG,"font_min_px":40,"font_max_px":40}
        plan,glyphs,final,growth = fit_with_expansion("one two three four five six",regions,resolve_font(config),config)
        self.assertIsNone(plan)
        self.assertFalse(growth)
        self.assertEqual([r["bbox"] for r in final],[r["bbox"] for r in regions])
        self.assertTrue(all(len(r["blocked_directions"]) == 4 for r in final))

    def test_exhausted_body_returns_review_without_escaping_mask(self):
        allowed = np.zeros((90,90),dtype=bool)
        allowed[10:80,10:80] = True
        regions = [region("a",[30,30,50,50],allowed)]
        config = {**DEFAULT_CONFIG,"font_min_px":24,"font_max_px":24}
        plan,glyphs,final,growth = fit_with_expansion("unbreakable"*30,regions,resolve_font(config),config)
        self.assertIsNone(plan)
        self.assertEqual(glyphs,[])
        self.assertTrue(growth)
        self.assertFalse(np.any(final[0]["mask"] & ~allowed))
        self.assertEqual(len(final[0]["blocked_directions"]),4)

    def test_float_ocr_bounds_and_overlap_rejection(self):
        config = {**DEFAULT_CONFIG,"body_margin_px":1}
        row = {"id":"u","bbox":[10,10,100,80],"source_boxes":[{
            "source_text_id":"ocr1","bbox":[10.2,10.2,100.8,80.8],"reading_order":0}]}
        regions = ocr_regions(None,row,{},(150,100),config)
        self.assertEqual(regions[0]["bbox"],[11,11,100,80])
        row["source_boxes"].append({**row["source_boxes"][0],"source_text_id":"ocr2","reading_order":1})
        with self.assertRaisesRegex(ValueError,"Overlapping"):
            ocr_regions(None,row,{},(150,100),config)


if __name__ == "__main__":
    unittest.main()
