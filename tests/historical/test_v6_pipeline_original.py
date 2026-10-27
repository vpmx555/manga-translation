"""Behavioral fixtures for v6 topology, identities, effects and glyph containment."""
import copy
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from manga_pipeline.pipeline import Pipeline, load_config
from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.v6 import persons, semantic, visual
from manga_pipeline.v6.normalize import normalize
from manga_pipeline.v6.stages import geometry
from manga_pipeline.v6.typeset import fit, resolve_font
from manga_pipeline.v6.translation import shorten
from manga_pipeline.visual.geometry import read_mask
from manga_pipeline.visual.stages import layout


def joined_mask(size=(220,140)):
    image = Image.new("L",size)
    draw = ImageDraw.Draw(image)
    draw.ellipse((10,10,95,100),fill=255)
    draw.ellipse((120,10,205,100),fill=255)
    draw.rectangle((85,52,130,56),fill=255)
    return np.asarray(image)>0


class Backend:
    def detect(self,image):
        mask = joined_mask(image.size)
        return {"panels":[{"bbox":[0,0,110,140],"confidence":.9},{"bbox":[110,0,220,140],"confidence":.9}],
                "bubbles":[{"mask":mask,"bbox":[10,10,206,101],"confidence":.9}],
                "onomatopoeia":[{"bbox":[20,110,80,135],"confidence":.9}]}


class Detector:
    def detect(self,text):
        if text == "Clench":
            return [{"name":"Clench","start":0,"end":6,"source":"ner"}]
        return []


class Provider:
    def __init__(self):
        self.calls = []

    def infer(self,task,prompt,data,schema,validator,images=None):
        self.calls.append(task)
        if task == "analyze_scene_v6":
            result = {"scenes": [{"start_id": next(r['id'] for r in data['targets'] if r['scene_target']), "summary": "A greeting."}],
                      "names": [{"utterance_id": r["id"], "name": "Clench"}
                                for r in data["targets"] if r["text"] == "Clench"],
                      "name_normalization": {"patches": [], "groups": [], "suspicions": []}}
        elif task == "analyze_dialogue_v6":
            rows = []
            for target in data["targets"]:
                mentions = [{"candidate_id":m["id"],"confirmed_person_name":False,"mention_type":"unknown",
                    "target_person_id":None,"recipient_cluster_ref":None,"visual_support":"unknown",
                    "components":[],"evidence":"Action description, not a name"}
                    for m in data["mentions"] if m["utterance_id"] == target["id"]]
                rows.append({"utterance_id":target["id"],"content_type":"action_state" if target["text"] == "Clench" else "dialogue",
                    "speech_style":"unknown","speaker_person_id":None,"speaker_cluster_ref":None,
                    "addressee_person_ids":[],"mentions":mentions,"evidence":"Ambiguous speaker remains Other",
                    "speaker_kind":"others", "addressee_type":"unknown", "evidence_refs":[target["id"]]})
            result = {"utterances":{r["utterance_id"]:{k:v for k,v in r.items() if k != "utterance_id"} for r in reversed(rows)},
                      "distinct_person_pairs":[]}
        else:
            result = {"translations":[{"id":target["id"],"translation":"Siết chặt" if target["text"] == "Clench" else "Xin chào bạn hôm nay",
                "needs_review":False,"review_reason":"","glossary_proposals":[]} for target in data["targets"]]}
            result["translations"] = {r["id"]:{k:v for k,v in r.items() if k != "id"} for r in reversed(result["translations"])}
        validator(result)
        return result

    def infer_once(self,*args,**kwargs):
        return self.infer(*args,**kwargs)


class V6Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = load_config()
        self.cfg["v6"].update(font_min_px=8,font_max_px=22)

    def fixture(self):
        image = self.root/"page.png"
        pixels = Image.new("RGB",(220,140),"white")
        for box in ((35,35,75,65),(145,35,185,65),(20,112,80,134)):
            pixels.paste((0,0,0),box)
        pixels.save(image)
        bank = self.root/"banks/story/characters.json"
        atomic_json(bank,{"characters":{"0":{"id":0,"display_name":"Arase","aliases":[]}}})
        source = {"story":"story","chapter":"chapter","folder":str(self.root),
                  "images":[{"path":str(image),"sha256":file_hash(image)}]}
        store = RunStore.create(self.root/"run",source=source,config=self.cfg,bank_path=bank)
        raw = {"schema_version":1,"kind":"magi_raw","reading_direction":"rtl","document_id":"doc",
               "story_id":"story","chapter_id":"chapter","pages":[{"id":"p1","page":1,"image_path":str(image),
                   "width":220,"height":140,"panels":[[0,0,110,140],[110,0,220,140]],"tails":[],
                   "characters":[[0,100,20,140]],"character_cluster_labels":[0],"character_ids":[0]}],"texts":[]}
        for index,(text,box) in enumerate([("Hello",[35,35,75,65]),("friend",[145,35,185,65]),("Clench",[20,112,80,134])]):
            raw["texts"].append({"id":f"p1:t{index}","page_id":"p1","page":1,"text_index":index,"reading_order":index,
                "text":text,"bbox":box,"panel_index":int(index==1),"speaker_cluster_id":"p1:c0",
                "speaker_id":0,"character_detection_index":0,"is_essential_text":index<2})
        store.finish("layout",layout(store,Backend()))
        store.finish("extract",raw)
        return store,raw

    def test_connected_lobes_keep_bridge_and_cross_panel_group(self):
        store,raw = self.fixture()
        result = geometry(store)
        group = result["pages"][0]["bubble_groups"][0]
        self.assertEqual(group["panel_indices"],[0,1])
        self.assertGreaterEqual(len(group["parts"]),2)
        bridge = read_mask(store.directory,group["bridge_mask"],(220,140))
        self.assertTrue(bridge[54,110])
        self.assertTrue(group["connections"])
        for part in group["parts"]:
            body = read_mask(store.directory,part["body_mask"],(220,140))
            self.assertFalse(np.any(body & bridge))
            for ring in part["body_rings"]:
                self.assertEqual(ring["points"][0],ring["points"][-1])
        document = normalize(result)
        dialogue = next(r for r in document["utterances"] if r["group_id"])
        self.assertEqual(dialogue["text"],"Hello friend")
        self.assertEqual(len(dialogue["source_text_ids"]),2)
        self.assertIsNone(dialogue["speaker_person_id"])
        self.assertEqual(raw["texts"][0]["speaker_id"],0)

    def test_disconnected_large_bubbles_do_not_form_one_utterance(self):
        mask = joined_mask()
        mask[:,96:120] = False
        page = {"id":"p1","panels":[],"tails":[]}
        groups,_ = visual.build_groups(self.root,self.root/"masks",page,[{"id":"candidate"}],[mask],
            np.full((140,220,3),255,dtype=np.uint8),self.cfg["v6"])
        self.assertEqual(len(groups),2)

    def test_holes_and_multiple_rings_are_closed(self):
        mask = np.zeros((60,60),bool)
        mask[4:40,4:40] = True
        mask[12:20,12:20] = False
        mask[45:55,45:55] = True
        rings = visual.rings(mask)
        self.assertEqual(sum(r["kind"]=="outer" for r in rings),2)
        self.assertEqual(sum(r["kind"]=="hole" for r in rings),1)
        self.assertTrue(all(r["points"][0]==r["points"][-1] for r in rings))

    def test_ocr_changes_do_not_invalidate_visual_targets(self):
        store,raw = self.fixture()
        first = geometry(store)
        files = {p:file_hash(p) for p in (store.directory/store.directories["bubble_geometry"]/"masks").rglob("*.png")}
        raw["texts"][0].update(text="different",bbox=[36,36,74,64],is_essential_text=False)
        store.finish("extract",raw)
        second = geometry(store)
        self.assertEqual(first["pages"][0]["bubble_groups"],second["pages"][0]["bubble_groups"])
        self.assertEqual(files,{p:file_hash(p) for p in files})
        with self.assertRaises(TypeError):
            visual.visual_hint(pixels=np.zeros((10,10,3)),body_mask=np.ones((10,10),bool),
                observed_mask=np.ones((10,10),bool),inferred_mask=np.zeros((10,10),bool),config=self.cfg["v6"],text="hello")

    def identity_state(self):
        state = {"schema_version":6,"persons":{},"name_index":{},"cluster_to_person":{},"redirects":{},
                 "calling_events":{},"operations":[],"observations":{},"cannot_links":[]}
        for person in ("a","b","c"):
            state["persons"][person] = {"person_id":person,"primary_name":None,"name_forms":[],"components":[],"appearance_refs":[]}
            state["cluster_to_person"][person] = person
            state["observations"][person] = [{"appearance_key":person}]
        return state

    def test_repeated_calls_override_dissimilar_images_and_replay_counts_once(self):
        state = self.identity_state()
        persons.add_name(state,"a","Arase -san",source="named",components=[{"kind":"surname","value":"Arase"}])
        event = {"event_type":"direct_name_call","confirmation_status":"confirmed","recipient_cluster_ref":"b",
                 "target_person_id":"a","visual_support":False,"source_occurrence_key":"one","event_id":"one"}
        self.assertEqual(persons.propose(state,[event,event])[0]["same_person_status"],"needs_review")
        second = {**event,"source_occurrence_key":"two","event_id":"two"}
        proposals = persons.propose(state,[event,second])
        self.assertEqual(proposals[0]["decision_basis"],"repeated_confirmed_calling_support")
        linked = persons.apply_links(state,proposals)
        self.assertEqual(linked["cluster_to_person"]["b"],"a")
        self.assertEqual(persons.canonical(linked,"b"),"a")
        self.assertEqual(linked["persons"]["a"]["name_forms"][0]["surface"],"arase-san")
        self.assertEqual(state["cluster_to_person"]["b"],"b")

    def test_alias_collision_and_transitive_contradiction_stay_unresolved(self):
        state = self.identity_state()
        for person,name in (("a","Hanako Sato"),("c","Taro Sato")):
            persons.add_name(state,person,name,source="confirmed",components=[{"kind":"surname","value":"Sato"}])
        self.assertEqual(set(state["name_index"]["sato"]),{"a","c"})
        state["cannot_links"] = [{"person_ids":["a","c"],"evidence":"Two distinct people called Sato"}]
        first = persons.apply_links(state,[{"cluster_ref":"b","person_id":"a","same_person_status":"supported"}])
        second = persons.apply_links(first,[{"cluster_ref":"c","person_id":"b","same_person_status":"supported"}])
        self.assertEqual(second["cluster_to_person"]["c"],"c")
        self.assertEqual(second["link_reviews"][0]["reason"],"semantic_cannot_link")
        with self.assertRaises(ValueError):
            persons.name_form("Other")

    def test_person_commit_idempotence_revert_and_source_bank_immutability(self):
        bank = self.root/"visual-bank/characters.json"
        atomic_json(bank,{"characters":{}})
        asset = bank.parent/"embedding.bin"
        asset.write_bytes(b"original visual bank")
        before = persons.bank_snapshot(bank)
        state = self.identity_state()
        state["base_registry_hash"] = fingerprint(None)
        path = self.root/"persons/registry.json"
        one = persons.commit(path,state,operation_id="op",source_bank=bank,expected_source_snapshot=before)
        two = persons.commit(path,state,operation_id="op",source_bank=bank,expected_source_snapshot=before)
        self.assertEqual(one,two)
        self.assertEqual(state["base_registry_hash"],fingerprint(None))
        self.assertEqual(before,persons.bank_snapshot(bank))
        persons.revert(path,"op")
        self.assertEqual(read_json(path)["persons"],{})
        asset.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            persons.commit(path,state,operation_id="new",source_bank=bank,expected_source_snapshot=before)

    def test_calls_on_separate_page_clusters_share_appearance_evidence(self):
        state = self.identity_state()
        state["observations"]["b"][0]["appearance_key"] = "visual-bank-b"
        state["observations"]["b2"] = [{"appearance_key":"visual-bank-b"}]
        state["cluster_to_person"]["b2"] = "b"
        base = {"event_type":"direct_name_call","confirmation_status":"confirmed","target_person_id":"a",
                "recipient_appearance_key":"visual-bank-b","visual_support":False}
        events = [{**base,"recipient_cluster_ref":"b","event_id":"e1","source_occurrence_key":"page-1"},
                  {**base,"recipient_cluster_ref":"b2","event_id":"e2","source_occurrence_key":"page-2"}]
        proposals = persons.propose(state,events)
        self.assertEqual(proposals[0]["confirmed_event_count"],2)
        self.assertEqual(set(proposals[0]["cluster_refs"]),{"b","b2"})
        linked = persons.apply_links(state,proposals)
        self.assertEqual(linked["cluster_to_person"]["b2"],"a")

    def test_split_honorific_is_indexed_without_changing_source_ocr(self):
        store,raw = self.fixture()
        store.manifest['config']['v6']['scan_policy'] = None  # Historical detector/index contract.
        store.manifest['config']['ner']['backend'] = 'gliner'
        raw["texts"][0]["text"] = "Arase -san"
        store.finish("extract",raw)
        document = normalize(geometry(store))
        result = semantic.scan(store,document,raw,Detector(),provider=Provider())
        address = next(m for m in result["candidates"] if m["key"]=="arase-san")
        self.assertEqual(address["name"],"arase-san")
        self.assertEqual(address["raw_surface"],"Arase -san")
        self.assertEqual(raw["texts"][0]["text"],"Arase -san")

    def test_visual_cache_key_changes_with_image_pixels(self):
        mask = joined_mask()
        page = {"id":"p1","panels":[],"tails":[]}
        pixels = np.full((140,220,3),255,dtype=np.uint8)
        before,_ = visual.build_groups(self.root,self.root/"before",page,[{"id":"b"}],[mask],pixels,self.cfg["v6"])
        pixels[0,0] = 0
        after,_ = visual.build_groups(self.root,self.root/"after",page,[{"id":"b"}],[mask],pixels,self.cfg["v6"])
        self.assertNotEqual(before[0]["parts"][0]["visual_cache_key"],after[0]["parts"][0]["visual_cache_key"])

    def test_rejected_shortening_is_persisted_and_not_replayed(self):
        class Store:
            def __init__(self):
                self.saved = {}
            def read(self,_):
                return {"translation_resources":{"glossary":[]}}
            def target(self,step,key,request):
                return self.saved.get((key,fingerprint(request)))
            def commit_target(self,step,key,request,*,result=None):
                self.saved[(key,fingerprint(request))] = result
        class BadShortener:
            def __init__(self):
                self.calls = 0
            def infer_once(self,task,prompt,data,schema,validator):
                self.calls += 1
                value = {"translation":"arase-san có 3 người","meaning_preserved":True,"review_reason":""}
                validator(value)
                return value
        store,provider = Store(),BadShortener()
        row = {"id":"u","text":"Arase-san, there are not 3 people.","mentions":[]}
        for _ in range(2):
            with self.assertRaises(ValueError):
                shorten(store,provider,row,"natural","arase-san không có 3 người ở đây",{"usable_lines":1},1)
        self.assertEqual(provider.calls,1)

    def test_v6_inpaint_waits_for_semantics_and_preserves_effect_pixels(self):
        self.cfg["inpaint"]["enabled"] = True
        store,raw = self.fixture()
        self.assertIn("export",store.dependencies("inpaint"))
        provider = Provider()
        runner = Pipeline(store,provider=provider,translation_provider=provider,detector=Detector())
        self.assertTrue(runner.run())
        result = runner.store.read("inpaint")["pages"][0]
        self.assertEqual(result["aot_calls"],0)
        self.assertEqual(len(result["decisions"]),2)
        with Image.open(runner.store.directory/result["clean_path"]) as image:
            self.assertEqual(image.getpixel((30,120)),(0,0,0))
            self.assertEqual(image.getpixel((50,50)),(255,255,255))
        self.assertTrue(all(p["render_kind"]=="clean_render" for p in runner.store.read("typeset")["pages"]))

    def test_nonwhite_v6_page_uses_one_mock_aot_pass(self):
        from tests.test_visual_pipeline import InpaintBackend
        self.cfg["inpaint"]["enabled"] = True
        original = Image.new
        def gray_page(mode, size, color=0):
            return original(mode, size, (120,120,120) if mode == "RGB" and size == (220,140) and color == "white" else color)
        with patch("PIL.Image.new", side_effect=gray_page):
            store, _ = self.fixture()
        provider, backend = Provider(), InpaintBackend()
        runner = Pipeline(store, provider=provider, detector=Detector(), inpaint_backend=backend)
        self.assertTrue(runner.run(until="inpaint"))
        self.assertEqual(backend.calls, 1)
        self.assertEqual(runner.store.read("inpaint")["pages"][0]["aot_calls"], 1)

    def test_balanced_group_glyphs_share_font_and_never_cross_bridge(self):
        mask = joined_mask()
        bodies,bridge,_,_ = visual.partition(mask,[],self.cfg["v6"])
        parts = [{"id":str(i),"mask":body,"anchor":[50+110*i,55]} for i,body in enumerate(bodies)]
        text = "Hôm nay chúng ta cùng đến trường và gặp bạn bè mới"
        plan,glyphs = fit(text,parts,resolve_font(self.cfg["v6"]),self.cfg["v6"])
        self.assertIsNotNone(plan)
        self.assertTrue(all(item["lines"] for item in plan["parts"]))
        self.assertEqual(" ".join(line["text"] for item in plan["parts"] for line in item["lines"]),text)
        for part,glyph in zip(parts,glyphs):
            pixels = np.asarray(glyph)>0
            self.assertFalse(np.any(pixels & ~part["mask"]))
            self.assertFalse(np.any(pixels & bridge))

    def test_complete_local_flow_routes_effects_and_resumes_without_model_calls(self):
        store,raw = self.fixture()
        provider = Provider()
        bank_before = persons.bank_snapshot(store.manifest["bank_path"])
        runner = Pipeline(store,provider=provider,translation_provider=provider,detector=Detector())
        self.assertTrue(runner.run())
        store = runner.store
        self.assertEqual(store.manifest["pipeline_version"],6)
        exported = store.read("export")
        self.assertEqual(len(exported["utterances"]),2)
        self.assertEqual(exported["effects"],[])
        self.assertEqual(exported["utterances"][1]["text"],"Clench")
        self.assertEqual(exported["utterances"][1]["semantic_resolution"],"source_only")
        self.assertNotIn("clench",store.read("build_identity")["person_state"]["name_index"])
        self.assertIsNone(exported["utterances"][0]["speaker_person_id"])
        self.assertEqual(bank_before,persons.bank_snapshot(store.manifest["bank_path"]))
        result = store.read("typeset")
        self.assertEqual(result["failures"],[])
        self.assertEqual(len(result["pages"]),1)
        self.assertTrue(all(p["render_kind"]=="placement_preview_original_text_present" for p in result["pages"]))
        self.assertFalse(result["effects_image_edits"])
        calls = len(provider.calls)
        self.assertTrue(runner.run())
        self.assertEqual(len(provider.calls),calls)
        self.assertTrue((store.directory/"review.md").exists())
        path = store.directory/next(iter(result["pages"][0]["files"]))
        path.write_bytes(b"corrupted")
        with self.assertRaises(ValueError):
            store.read("typeset")


if __name__ == "__main__":
    unittest.main()
