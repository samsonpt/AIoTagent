# M5 Guard銆佸鎵逛笌鍝堝笇閾捐拷婧?Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 瀹炵幇鎬荤嚎绾?ActionGuard锛堜笁閬撳叧 + 鎬ュ仠蹇€熼€氶亾锛夈€佸鎵归槦鍒椾笌 HumanModel 妯℃嫙瀹℃壒銆丼QLite 鏃佽矾鍝堝笇閾炬牎楠屼笌闄嶇骇锛屼娇鏈洊绔犲懡浠ゆ棤娉曞埌杈?Plant 鎵ц銆?

**Architecture:** `ActionGuard`锛坄client_id="guard"`锛夎闃?`plant/+/+/command` 涓?`plant/line/command`锛涘拷鐣ュ凡鐩栫珷娑堟伅锛涙牎楠屽悗鍚屼富棰樿浆鍙戝苟闄勫姞 `guarded: true`銆侾lant 涓㈠純鏈洊绔犺浇鑽枫€傚鎵瑰啓鍏?`approval_queue`锛岀敱鎵╁睍鍚庣殑 `HumanModel` 鎸変豢鐪熸椂閽熷喅绛栧苟鍥炶皟 Guard銆俙trace_chain` 鏃佽矾鍥涚被鍩嬬偣锛涙牎楠屽け璐ュ悗浠呯櫧鍚嶅崟涓庡凡鎵瑰噯浜哄伐鍔ㄤ綔鍙嚜鍔ㄧ洊绔犮€?

**Tech Stack:** Python 3.12, pydantic v2, numpy, sqlite3, pytest銆?

**瑙勬牸锛?* `docs/superpowers/specs/2026-10-09-m5-guard-trace-design.md`锛涚埗瑙勬牸 搂4.6銆伮?銆伮?.5銆伮?0 M5锛汚DR-006銆?

## Global Constraints

- Python 3.12锛涗緷璧栫敤 `pyproject.toml` 绠＄悊锛涙祴璇曠敤 `pytest`銆?
- 鎵€鏈夐殢鏈鸿繃绋嬫帴鍙?`seed`锛涘悓涓€ seed 蹇呴』寰楀埌閫愪綅涓€鑷寸殑缁撴灉銆傞殢鏈烘暟涓€寰嬮€氳繃 `common.rng.make_rng(seed, stream)` 鑾峰彇銆?
- 娑堟伅鎬荤嚎鎶借薄涓?`Bus` 鎺ュ彛锛涘崟鍏冩祴璇曚笉渚濊禆 Mosquitto銆?
- 浠跨湡鏃堕棿鐢?`SimClock` 椹卞姩銆傜姝娇鐢?`time.time()` / `datetime.now()` 浣滀负浠跨湡鏃堕棿銆?
- 浠ｇ爜鏍囪瘑绗︾敤鑻辨枃锛涙枃妗ｅ拰鏃ュ織璇存槑鐢ㄤ腑鏂囥€?
- 鍥涚被鍩嬬偣瀛楁鍙兘鏂板锛屼笉鑳戒慨鏀硅涔夛紙鏈噷绋嬬浠呮柊澧?`approval_queue` / `trace_chain` 琛級銆?
- `twin/`銆乣edge/`銆乣cloud/`銆乣guard/` 涓嶅厑璁?import `sim` 鍖呯殑浠讳綍妯″潡銆?
- JSON 搴忓垪鍖栵細`json.dumps(obj, sort_keys=True, ensure_ascii=False)`銆?
- 娴嬭瘯鏀惧湪 `tests/<鍖呭悕>/test_*.py`锛沗--import-mode=importlib`锛宍pythonpath = ["."]`銆?
- 涓嶅啓瑙ｉ噴鈥滆繖琛屽仛浠€涔堚€濈殑娉ㄩ噴銆?

## File Structure

```
guard/__init__.py
guard/policy.py              # 鐧藉悕鍗曘€侀珮椋庨櫓銆乧ommand鈫抰win 鏄犲皠銆侀棬妲涢槇鍊?
guard/chain.py               # entry_hash / 绾嚱鏁帮紙鍙€夎杽灏佽锛涗富 API 鍦?TraceStore锛?
guard/action_guard.py        # ActionGuard Controller
bench/schema.py              # approval_queue + trace_chain API锛況ecord_episode 閽?decide 閾?
bench/human_model.py         # 瀹℃壒闃熷垪鍐崇瓥
sim/plant.py                 # guarded 闂ㄩ棭
sim/runner.py                # 鎸傝浇 ActionGuard + 瀹℃壒鐢?HumanModel
edge/factory.py              # HumanModel 鎸傝浇绛栫暐锛坥cap vs approval锛?
tests/guard/test_*.py
tests/sim/test_plant.py      # send() 榛樿甯?guarded=True锛堝崟娴嬫ā鎷熷凡鐩栫珷锛?
tests/bench/test_schema.py   # 閾句笌瀹℃壒琛?
tests/bench/test_human_model.py
```

## 鏁板€间笌璇箟绾﹀畾

- **蹇€熼€氶亾锛?* `{stop, resume, change_bit}` 鈥?浠呮煡鍛戒护宸茬煡 + process/equipment 鍚堟硶锛涜烦杩囧叧 2/3锛涗笉鍏ュ鎵归槦銆?
- **浼€犵洊绔狅細** 鍏ョ珯杞借嵎宸叉湁 `guarded is True` 鈫?Guard **蹇界暐**锛堥槻鐜矾锛夈€傝嫢鍙戦€佹柟鏄潪 `guard` 涓旇嚜甯?`guarded: true`锛孭lant 渚т篃浼氭墽琛屸€斺€斿洜姝?**Plant 鍙俊浠荤洊绔狅紝Guard 鏄敮涓€鍚堟硶鐩栫珷鑰?*锛涙祴璇曚腑浼€犲満鏅細鐢?spy 浠?`edge` 韬唤鍙?`guarded:true` 鏃讹紝Guard 蹇界暐璇ユ秷鎭紙涓嶉噸鐩栫珷锛夛紝浣?Plant 浼氭墽琛屸€斺€旇鏍笺€岃嫢鑷甫鍒?Guard 瑙嗕负浼€犲苟鎷掔粷銆嶈惤瀹炰负锛欸uard 瀵?*鏈洊绔?*璺緞鍋氭牎楠岋紱瀵?*宸茬洊绔犱笖 sender鈮爂uard`** 鐨勬秷鎭紝Guard 棰濆鍐欐嫆缁?action 骞?*涓?*杞彂锛圥lant 鑻ュ凡鍚屾鏀跺埌鍚屾潯鍒欑珵鎬侊級銆?*閽夋瀹炵幇锛?* Bus 鍚屾鎶曢€掍笅锛孏uard 涓?Plant 鍧囪闃咃紱涓洪伩鍏嶄吉閫犵洊绔犺 Plant 鎵ц锛孭lant 鍦?`_execute` 瑕佹眰 `guarded is True` **涓?*锛堝彲閫夛級涓嶆牎楠?sender銆傝鏍艰姹傘€屼吉閫犳嫆缁濄€嶁啋 Guard 鍦ㄨ闃呭洖璋冮噷鑻ュ彂鐜?`guarded is True` 涓?`payload.get("_guard_stamp") != "guard"`锛屽垯瑙嗕负浼€狅細涓嶈浆鍙戙€佸啓鎷掔粷锛汸lant 鍙帴鍙楀甫 `_guard_stamp=="guard"` 鎴栫畝鍗曟柟妗堬細**浠呮帴鍙?`guarded is True`锛屼笖杈圭紭/浜戠鍙戝竷鏃跺墺绂讳换浣?`guarded` 閿?*銆?

  **鏈€缁堥拤姝伙紙绠€鍗曞彲闈狅級锛?*
  1. 杈圭紭/浜戠/Human OCAP 鍙戝竷鍛戒护鏃?*涓嶅緱**甯?`guarded`锛涜嫢甯︿簡锛孏uard 鍏ョ珯鏃惰嫢 `guarded is True` 鍒欏綋浣滀吉閫狅細**鍒犻櫎闃熷垪鎰忎箟**鈥斺€斿啓 `accepted=false` 鎷掔粷 action + chain锛?*涓嶈浆鍙?*锛汸lant 渚э細`guarded is not True` 鈫?闈欓粯涓㈠純銆?
  2. Guard 杞彂鏃惰缃?`guarded: True`銆乣guard_reason: str`銆?
  3. Guard 蹇界暐鑷繁杞彂鐨勬秷鎭細`payload.get("guarded") is True` 鈫?return锛堣嚜宸辩殑杞彂涔熶細杩涘洖璋冿紝鐩存帴蹇界暐锛岄伩鍏嶇幆璺級銆?*涓庝吉閫犲啿绐侊細** 浼€犱篃甯?`guarded:True`锛岃嫢鐩存帴蹇界暐鍒欎笉鍐欐嫆缁濄€?

  **淇閽夋锛?*
  - 鍏ョ珯鑻?`guarded is True`锛?*涓€寰嬪拷鐣?*锛堥槻鐜矾锛夈€備吉閫犵洊绔犻潬 **Plant 涓嶆墽琛屾湭鐩栫珷** + **鍙戝竷鏂硅鑼冧笉甯?guarded**锛涘彟鍔犳祴璇曪細杈圭紭鍙戝竷甯?`guarded:True` 鏃讹紝鍥?Guard 蹇界暐銆?*Plant 浼氭墽琛?*鈥斺€斾笉鍙帴鍙椼€?
  - **鍥犳 Plant 鏀逛负锛?* 浠呭綋 `guarded is True` **涓?* `payload.get("guard_id") == "guard"` 鏃舵墽琛屻€侴uard 鐩栫珷鏃跺啓鍏?`guard_id: "guard"`銆備吉閫犺嫢鎶勮繖涓や釜瀛楁浠嶅彲閫氳繃鈥斺€斿彲鎺ュ彈涓?MVP锛堜俊浠绘€荤嚎鍐呭鎴风锛夛紱棰濆锛欸uard 瀵规棤 `guard_id` 鐨?`guarded:True` 鍐欏憡璀︽嫆缁濓紙鍙€夛級銆侻VP 鐢?`guarded is True` + `guard_id == "guard"`銆?

- **鍏?1锛?* `bound_command(recipe, process, command, params)`锛沗None` 鈫?鎷掔粷銆俙process=="line"` 鏃惰烦杩?bound_command 宸ヨ壓绐楀彛锛屼粎鍏佽 `hold_lot`/`scrap_lot`銆?
- **鍏?2 闂ㄦ锛堥攣瀹氾級锛?* `y_min = 0.90 + 0.05 * (1 - confidence)`锛沗oos_max = 0.15 * confidence + 0.05`銆傞€氳繃鏉′欢锛歚pred.yield_prob >= y_min and pred.oos_prob <= oos_max`銆俙confidence`锛氳嫢 twin 鏈?`predictions`锛屽垯 `twin_confidence([o.q05 <= o.y <= o.q95 for o in twin.predictions])`锛涘惁鍒?`0.5`銆傝嫢 `use_twin_confidence_gate` 涓?False锛屽垯 `confidence` 鍥哄畾鎸?`1.0` 浠ｅ叆鍏紡锛堥棬妲涙渶鏉撅級銆俙confidence < 0.5` 涓?`use_twin_confidence_gate` 鈫?涓嶈嚜鍔ㄦ斁琛岋紙杞叧 3 鎴栨嫆缁濓級銆?
- **command鈫抰win 鏄犲皠锛堟棤娉曟槧灏勫垯璺宠繃鍏?2锛宒etail warning锛夛細**

  | command | kind | 鍚堝苟杩?simulate 鐨?params 閿?|
  |---------| |------|------------------------------|
  | `set_current_density` | thickness | `asd` 鈫?params[`asd`]锛涘叾浣欑敤 recipe 榛樿 `time_min`/`additive_ml_l` |
  | `set_bath_temp` | thickness | 鐢ㄥ綋鍓?榛樿 thickness params锛堟俯搴︿笉杩涙満鐞嗗垯 **璺宠繃鍏?2**锛?|
  | `dose_additive` | thickness | `additive_ml_l` 鈫?params |
  | `set_etch_temp` | width | `temp_c` |
  | `set_conveyor_speed` | width | `speed_m_min` |
  | `set_spray_pressure` | width | `spray_bar` |
  | `adjust_sg` | width | `sg` |
  | `set_rpm` / `set_feed` | roughness | 鐢?recipe/榛樿 roughness 鍙傛暟锛涜嫢妯″瀷鍙渶 hits 鍒?**璺宠繃鍏?2** |
  | 缁存姢绫?`clean_nozzle`/`repair_*` | 鈥?| 璺宠繃鍏?2 |

  瀹炵幇鍑芥暟锛歚map_command_to_twin(recipe, process, command, params) -> tuple[str, dict] | None`銆?

- **楂橀闄╋紙鍏?3锛夛細** `hold_lot`銆乣scrap_lot`锛涘叧 2 鏈€氳繃杞叆锛沗param_tune` 涓旂浉瀵?recipe 绐楀彛 target 鐨勭浉瀵瑰亸宸?`|v - target| / max(high-low, 1e-9) > 0.25`锛堝井璋冮槇鍊?**0.25**锛夈€傜淮鎶?鍔犺嵂/鎹㈤拡涓嶅洜璇ラ槇鍊艰繘鍏?3锛堟崲閽堝凡鍦ㄥ揩閫熼€氶亾锛夈€?
- **瀹℃壒寤惰繜锛?* `approval_delay_s = 900`锛?5 浠跨湡鍒嗛挓锛夈€俙clock.now >= t_submit + approval_delay_s` 鏃跺喅绛栥€?
- **瀹℃壒姒傜巼锛?* `make_rng(seed, "human_approval")`锛涜嫢 `process` 涓庡綋鍓嶆椿鍔ㄧ湡鍊兼晠闅滃伐搴忎竴鑷达紙`store.faults()` 涓?`t_cleared is None` 涓?`t_start <= now` 鐨勪换涓€ `process` 鍖归厤锛夆啋 `rng.random() < 0.9` 鎵瑰噯锛涘惁鍒?`rng.random() < 0.9` 椹冲洖锛堝嵆鍖归厤鏃?0.9 鎵癸紝涓嶅尮閰嶆椂 0.9 椹?= `random() >= 0.1` 鏃堕┏鍥炩€?瑙勬牸锛氫竴鑷翠互 0.9 鎵瑰噯锛屽惁鍒欎互 0.9 椹冲洖 鈫?涓嶅尮閰嶆椂 `rng.random() < 0.9` 鈫?椹冲洖锛夈€?
- **鍝堝笇锛?* `prev_hash` 鍒涗笘 `"GENESIS"`锛沗entry_hash = sha256((prev_hash + "\n" + payload).encode("utf-8")).hexdigest()`锛沺ayload 涓?canonical JSON 瀛楃涓层€?
- **閾惧啓鍏ワ細** act 鐢?Guard 鏀捐/鎷掔粷鏃讹紱decide 鍦?`TraceStore.record_episode` 鎴愬姛鍚庤嚜鍔?`append_chain(kind="decide", ...)`锛泂ense 鏈噷绋嬬鍙€夛紙鏃犲己鍒舵祴璇曪級銆?
- **鏍￠獙澶辫触闄嶇骇锛?* `ActionGuard.auto_actions_enabled = False`锛涗粎蹇€熼€氶亾涓?`resolve_approval(..., approved=True)` 鍙洊绔犮€?
- **runner锛?* `ablation is not None` 鏃跺垱寤哄苟鎸傝浇 `ActionGuard`锛堝叧 1 濮嬬粓鐢熸晥锛夛紱娉ㄥ叆 `bus, store, recipe, clock, twin, seed, ablation`銆俙use_human_gate` 鏃剁‘淇?HumanModel 甯﹀鎵硅兘鍔涘湪鍒楄〃涓紙瑙?Task 5锛夈€?
- **lot_id 閾鹃敭锛?* `params.get("lot_id")` 鎴?episode/`"default"`銆?

---

### Task 1: TraceStore 鈥?approval_queue 涓?trace_chain

**Files:**
- Modify: `bench/schema.py`
- Test: `tests/bench/test_schema.py`锛堣拷鍔犵敤渚嬶級锛涙柊寤?`tests/guard/test_chain.py` 浜﹀彲浣嗘湰浠诲姟鏀?schema 娴?

**Interfaces:**
- 琛?`approval_queue`锛歚request_id TEXT PK`, `t_submit REAL`, `process TEXT`, `equipment TEXT`, `command TEXT`, `params TEXT`, `source TEXT`, `lot_id TEXT`, `topic TEXT`, `status TEXT`, `t_decide REAL`, `decider TEXT`, `reason TEXT`
- 琛?`trace_chain`锛歚seq INTEGER PK AUTOINCREMENT`, `lot_id TEXT`, `kind TEXT`, `ref TEXT`, `t REAL`, `payload TEXT`, `prev_hash TEXT`, `entry_hash TEXT`
- `_ORDER` 澧炲姞涓よ〃锛沗dump()` 鑷姩瑕嗙洊
- `enqueue_approval(...) -> str` 杩斿洖 `request_id`锛坄uuid4` hex 鎴?`f"apr-{n}"` 鍗曡皟锛?
- `list_approvals(status: str | None = None) -> list[dict]`
- `update_approval(request_id, *, status, t_decide, decider, reason) -> None`
- `append_chain(*, lot_id: str, kind: str, ref: str, t: float, payload: dict) -> str` 杩斿洖 `entry_hash`
- `verify_chain(lot_id: str | None = None) -> tuple[bool, str]`
- `record_episode`锛氬湪鍐欏叆鍚庤嫢 `t_decide is not None`锛岃皟鐢?`append_chain(kind="decide", lot_id=detail.get("lot_id","default"), ref=episode_id, t=t_decide or t_detect, payload={episode 鎽樿})`

- [ ] **Step 1: 澶辫触娴嬭瘯**

```python
def test_trace_chain_tamper_detected():
    store = TraceStore()
    h1 = store.append_chain(lot_id="L1", kind="act", ref="a1", t=0.0, payload={"command": "stop"})
    store.append_chain(lot_id="L1", kind="act", ref="a2", t=1.0, payload={"command": "resume"})
    assert store.verify_chain("L1")[0] is True
    store._conn.execute("UPDATE trace_chain SET payload = ? WHERE ref = 'a1'", ['{"command":"hacked"}'])
    store._conn.commit()
    ok, reason = store.verify_chain("L1")
    assert ok is False and reason
```

- [ ] **Step 2:** `python -m pytest tests/bench/test_schema.py::test_trace_chain_tamper_detected -q` 鈫?FAIL锛堟棤鏂规硶锛夈€?
- [ ] **Step 3:** 鎵╁睍 `_SCHEMA`銆佸疄鐜?API锛涘彟娴?`enqueue_approval` / `update_approval` 涓庡垱涓?`prev_hash=="GENESIS"`銆?
- [ ] **Step 4:** 娴嬭瘯閫氳繃銆?
- [ ] **Step 5:** 鎻愪氦 `feat: approval_queue 涓?trace_chain 瀛樺偍 API`

---

### Task 2: guard.policy 鈥?鐧藉悕鍗曘€佹槧灏勩€侀棬妲涖€侀珮椋庨櫓

**Files:**
- Create: `guard/__init__.py`, `guard/policy.py`
- Test: `tests/guard/test_policy.py`

**Interfaces:**
- `FAST_PATH: frozenset[str] = frozenset({"stop", "resume", "change_bit"})`
- `HIGH_RISK_COMMANDS: frozenset[str] = frozenset({"hold_lot", "scrap_lot"})`
- `PARAM_TUNE_REL_THRESHOLD: float = 0.25`
- `map_command_to_twin(recipe, process, command, params) -> tuple[str, dict] | None`
- `twin_thresholds(confidence: float) -> tuple[float, float]` 鈫?`(y_min, oos_max)`
- `passes_twin_gate(pred, confidence: float) -> bool`
- `is_high_risk(recipe, process, command, params) -> bool`
- `confidence_from_twin(twin) -> float`锛坄twin is None` 鈫?`0.5`锛?

- [ ] **Step 1:**

```python
from guard.policy import twin_thresholds, passes_twin_gate, FAST_PATH
from twin.types import Prediction

def test_thresholds_tighten_when_confidence_low():
    y_hi, oos_hi = twin_thresholds(1.0)
    y_lo, oos_lo = twin_thresholds(0.0)
    assert y_hi == 0.90 and oos_hi == 0.20
    assert y_lo == 0.95 and oos_lo == 0.05
    pred = Prediction(mean=1.0, q05=0.9, q95=1.1, yield_prob=0.92, oos_prob=0.08)
    assert passes_twin_gate(pred, 1.0) is True
    assert passes_twin_gate(pred, 0.0) is False
```

- [ ] **Step 2鈥?:** 澶辫触 鈫?瀹炵幇 鈫?鍙︽祴 `set_current_density` 鏄犲皠闈?None銆乣clean_nozzle` 鏄犲皠 None銆乣is_high_risk("hold_lot")`銆?
- [ ] **Step 5:** 鎻愪氦 `feat: Guard 绛栫暐甯搁噺涓庡鐢熼棬妲沗

---

### Task 3: Plant 鐩栫珷闂ㄩ棭

**Files:**
- Modify: `sim/plant.py` `_execute` 寮€澶?
- Modify: `tests/sim/test_plant.py` 鈥?`send(..., guarded=True)` 榛樿鍐欏叆 `guarded=True`, `guard_id="guard"`
- Test: 鍚屾枃浠舵柊澧?`test_unguarded_command_discarded`

**Interfaces:**
- `_execute`锛氳嫢 `payload.get("guarded") is not True` 鎴?`payload.get("guard_id") != "guard"` 鈫?**return**锛堜笉鍐?action_log锛夈€?

- [ ] **Step 1:**

```python
def test_unguarded_command_discarded():
    plant, bus, store, _ = make()
    topic = topics.command("plating")
    bus.publish(topic, {"command": "set_current_density", "params": {"asd": 2.2}, "source": "edge", "reason": "x"}, "edge")
    steps(plant, 2)
    assert store.actions() == []
    assert plant.stations["plating"].current_density_asd == 2.0
```

- [ ] **Step 2:** 鍏堟敼 Plant 闂ㄩ棭浣胯娴嬭瘯閫氳繃锛涘啀鏀?`send()` 榛樿鐩栫珷锛屼慨澶嶆棦鏈?plant 娴嬭瘯銆?
- [ ] **Step 3:** `python -m pytest tests/sim/test_plant.py -q` 鍏ㄧ豢銆?
- [ ] **Step 4:** 鎻愪氦 `feat: Plant 浠呮墽琛?Guard 鐩栫珷鍛戒护`

---

### Task 4: ActionGuard 鏍稿績锛堝叧 1/2/3 + 蹇€?+ 閾?act锛?

**Files:**
- Create: `guard/action_guard.py`
- Test: `tests/guard/test_action_guard.py`

**Interfaces:**
- `class ActionGuard:`
  - `__init__(self, bus, store, recipe, clock, twin, seed: int, ablation: AblationConfig)`
  - `client_id = "guard"`
  - `auto_actions_enabled: bool = True`
  - 璁㈤槄 `plant/+/+/command` 涓?`topics.line_command()`
  - `on_tick(self, clock) -> None`锛氬彲绌哄疄鐜帮紙瀹℃壒涓嶅湪姝よ疆璇級
  - `resolve_approval(self, request_id: str, approved: bool, reason: str) -> None`
  - 鍐呴儴锛歚_on_command(topic, payload)`锛沗_stamp_forward(topic, payload, reason)`锛沗_reject(...)`锛沗_enqueue(...)`

**澶勭悊娴佺▼锛堥拤姝伙級锛?*
1. 鑻?`payload.get("guarded") is True`锛歳eturn锛堥槻鐜矾锛夈€?
2. 瑙ｆ瀽 `process`/`equipment`/`command`/`params`/`source`銆?
3. 鑻?`command in FAST_PATH`锛氬悎娉曞垯 `_stamp_forward`锛堝嵆浣?`auto_actions_enabled` 涓?False 涔熷厑璁革級銆?
4. 鑻ラ潪蹇€氫笖 `not auto_actions_enabled`锛歚_reject(reason="chain_invalid_auto_disabled")`銆?
5. 鍏?1锛歚line` 鍙厑 hold/scrap锛涘惁鍒?`bound_command`锛涘け璐?`_reject`銆?
6. 鍏?2锛氳嫢 `ablation.use_twin_lookahead and twin is not None` 涓斿彲鏄犲皠锛氱畻 confidence锛堝皧閲?`use_twin_confidence_gate`锛夛紱涓嶉€氳繃鍒欐爣 `needs_approval`锛涗笉鍙槧灏勫垯 detail warning 缁х画銆?
7. 鍏?3锛氳嫢 `needs_approval or is_high_risk(...)`锛氳嫢 `use_human_gate` 鈫?enqueue锛屼笉杞彂锛涘惁鍒欒嫢 `use_human_gate` 涓?False 鈫?鐩存帴 `_stamp_forward`锛堥珮椋庨櫓鑷姩杩囷級銆?
8. 鍚﹀垯 `_stamp_forward`銆?
9. `_stamp_forward`锛歞eepcopy payload锛岃 `guarded=True`, `guard_id="guard"`, `guard_reason=...`锛沗bus.publish(topic, payload, self.client_id)`锛沗record_action(accepted=True, reason=...)`锛沗append_chain(kind="act", ...)`銆?
10. `_reject`锛歚record_action(accepted=False, reason=f"rejected_by=guard;{reason}")`锛沗append_chain`锛涗笉 publish銆?

- [ ] **Step 1:**

```python
def test_fast_path_stop_stamped_and_executed():
    # bus+store+recipe+clock+Plant+ActionGuard锛沺ublish stop 鏃?guarded锛沢uard 鍥炶皟鍚?plant.step_tick
    ...
    assert any(a.command == "stop" and a.accepted for a in store.actions())
    assert plant.stations["drill"].stopped
```

```python
def test_envelope_reject_unknown_command():
    # publish levitate 鈫?鎷掔粷 action accepted=False锛宻tation 涓嶅彉
```

- [ ] **Step 2鈥?:** 瀹炵幇鏈€灏?Guard锛涜鐩?`use_human_gate=True` 鏃?`hold_lot` 鍏ラ槦涓嶈浆鍙戙€?
- [ ] **Step 5:** 鎻愪氦 `feat: ActionGuard 涓夐亾鍏充笌蹇€熼€氶亾`

---

### Task 5: HumanModel 瀹℃壒 + factory/runner 鎸傝浇

**Files:**
- Modify: `bench/human_model.py`
- Modify: `edge/factory.py`
- Modify: `sim/runner.py`
- Test: `tests/bench/test_human_model.py`锛沗tests/guard/test_approval.py`锛沗tests/guard/test_runner_guard.py`

**Interfaces:**
- `HumanModel.__init__(self, bus, recipe, clock, delay_ticks=2, *, store=None, guard=None, seed=0, ocap=True, approval_delay_s=900)`
  - `ocap=False` 鏃朵笉璁㈤槄 `plant/events/+`锛堟垨璁㈤槄浣嗕笉鍙戜护锛?
  - `store`+`guard` 闈炵┖鏃讹細`on_tick` 澶勭悊 `list_approvals("pending")`
- 鎵瑰噯锛歚update_approval(... approved)` + `guard.resolve_approval(request_id, True, reason)`
- 椹冲洖锛歚update_approval(... rejected)` + `guard.resolve_approval(request_id, False, reason)`锛圙uard 鍐欐嫆缁?action锛?
- `resolve_approval`锛氫粠闃熷垪琛屾仮澶?topic/payload锛屾壒鍑嗗垯 `_stamp_forward`锛?*涓嶅彈** `auto_actions_enabled` 闄愬埗锛夛紱椹冲洖鍒?`_reject`
- `make_controllers`锛?
  - `use_edge_agent=True` 鈫?edge agents锛涜嫢璋冪敤鏂归渶瑕佸鎵?HumanModel 鐢?runner 鍙﹀姞锛?*鎴?* factory 杩斿洖 `(agents, approval_human_spec)`鈥斺€?*閽夋锛?* factory 淇濇寔 edge 閫昏緫锛沗runner` 鍦?`ablation.use_human_gate` 鏃惰拷鍔?`HumanModel(..., store=store, guard=guard, seed=scenario.seed, ocap=not ablation.use_edge_agent)`銆?
  - 褰?`use_edge_agent=False`锛歠actory 浠嶈拷鍔?OCAP HumanModel锛況unner **涓嶈**鍐嶈拷鍔犵浜屼釜鈥斺€旇嫢 `use_human_gate`锛岀粰 factory 鐨?HumanModel 娉ㄥ叆 store/guard銆?*閽夋鏇寸畝锛?* 鎵╁睍 `make_controllers(..., guard=None)`锛汷CAP HumanModel 涓庡鎵瑰悎骞朵负涓€涓疄渚嬨€?
- `runner`锛氬垱寤?`guard = ActionGuard(...)` 鍚?`extra.append(guard)`锛沗make_controllers(..., guard=guard)`锛涢『搴忥細controllers锛堝惈 human锛夆啋 twin 鈫?cloud 鈫?**guard 搴斿厛浜?plant 娑堣垂锛?* 鍚?tick 鍐呭懡浠わ細edge.on_tick 鍙戝竷 鈫?Guard 鍚屾鍥炶皟鐩栫珷 鈫?鍏?Plant 闃熷垪 鈫?涓?tick 鎵ц銆侴uard 浣滀负璁㈤槄鑰呭嵆鍙紝涓嶅繀鍦?`extra` 鏈€鍓嶏紱浠?`extra.append(guard)` 浠ヤ究鏈潵 tick 閫昏緫銆?

- [ ] **Step 1:**

```python
def test_approval_delay_and_seed_stable():
    # enqueue hold_lot锛沘dvance now 鍒?+900锛沨uman.on_tick锛涘悓涓€ seed 涓ゆ鐘舵€佷竴鑷?
```

- [ ] **Step 2鈥?:** 瀹炵幇锛沗test_runner_guard_short_scenario`锛歚run(..., ablation=AblationConfig(), n_ticks=3)` 涓嶅穿婧冧笖 bus 鏈?`client_id=guard` 鎴?actions 鍚?guard 鐩稿叧 reason銆?
- [ ] **Step 5:** 鎻愪氦 `feat: HumanModel 瀹℃壒涓?runner 鎸傝浇 Guard`

---

### Task 6: 閾炬牎楠岄檷绾?+ 娑堣瀺 + 闅旂

**Files:**
- Modify: `guard/action_guard.py`锛堟毚闇?`check_chain()` 鎴栧湪 `on_tick` 璋冪敤 `verify_chain`锛?
- Test: `tests/guard/test_chain_degrade.py`锛沗tests/guard/test_ablation_human_gate.py`锛涗緷璧栨棦鏈?`tests/test_isolation.py`

**Interfaces:**
- `ActionGuard.on_tick`锛氳嫢 `verify_chain()[0] is False` 鈫?`auto_actions_enabled = False`
- 闄嶇骇鍚庯細`set_current_density` 鎷掔粷锛沗stop` 浠嶆斁琛岋紱`resolve_approval(True)` 浠嶆斁琛?
- `use_human_gate=False`锛氶珮椋庨櫓涓嶅叆闃燂紝鐩存帴鐩栫珷

- [ ] **Step 1鈥?:** 绡℃敼閾?鈫?on_tick 鈫?鑷姩鍔ㄤ綔鎷掔粷锛涘揩閫氫粛鍙紱isolation 鍏ㄧ豢銆?
- [ ] **Step 5:** 鎻愪氦 `feat: 鍝堝笇閾炬牎楠屽け璐ラ檷绾т笌娑堣瀺闂╜

---

### Task 7: 瀹夊叏娓呭崟涓?OODA 鐭泦鎴?

**Files:**
- Create: `tests/guard/test_security_guard.py`锛堥暅鍍?`tests/cloud/test_security_cloud.py` 椋庢牸锛氭棤 sim import銆佹棤瀵嗛挜纭紪鐮侊級
- Test: `tests/guard/test_ooda_smoke.py` 鈥?鐭満鏅細浜哄伐/娴嬭瘯鐩存帴鍚?command 涓婚鍙?`clean_nozzle` 鎴?`stop`锛岀粡 Guard 鍒?Plant

- [ ] **Step 1:** 瀹夊叏娴嬭瘯锛欰ST 鎴?`find_sim_imports` 宸茶鐩?guard锛涙湰鏂囦欢鏂█ `guard` 鍖呭瓨鍦ㄤ笖 `ActionGuard` 鏃?`import sim`銆?
- [ ] **Step 2:** OODA smoke锛歚run` 鎴栨墜鎼?Plant+Guard+publish stop 鈫?station stopped銆?
- [ ] **Step 3:** `python -m pytest tests/guard tests/sim/test_plant.py tests/bench/test_schema.py tests/bench/test_human_model.py tests/test_isolation.py -q`
- [ ] **Step 4:** 鎻愪氦 `test: M5 Guard 瀹夊叏涓?OODA 鍐掔儫`

---

## Spec coverage锛堣嚜瀹★級

| 瑙勬牸椤?| 浠诲姟 |
|--------|------|
| 鍛戒护璺緞 / 鐩栫珷 / Plant 闂ㄩ棭 | T3, T4 |
| 蹇€熼€氶亾 | T2, T4 |
| 鍏?1 bound_command | T4 |
| 鍏?2 瀛敓闂ㄦ鍏紡 | T2, T4 |
| 鍏?3 / 瀹℃壒闃熷垪琛?| T1, T4, T5 |
| HumanModel 寤惰繜涓?0.9 姒傜巼 | T5 |
| trace_chain / verify / 闄嶇骇 | T1, T6 |
| decide 閾鹃挬瀛?| T1 |
| runner 鎸傝浇 | T5 |
| 闅旂涓庡畨鍏?| T6, T7 |
| 娑堣瀺 use_human_gate | T6 |
| 涓嶅仛鐪嬫澘/鍖哄潡閾?| 閬靛畧 |

## Placeholder scan

鏃?TBD/TODO锛涢槇鍊间笌鏄犲皠宸查拤姝汇€?

## Type consistency

- `guarded` + `guard_id=="guard"` 璐┛ T3/T4
- `resolve_approval(request_id, approved, reason)` 璐┛ T4/T5
- `append_chain` / `verify_chain` 璐┛ T1/T4/T6
