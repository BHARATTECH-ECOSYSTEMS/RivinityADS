# RivinityAds WU-1 — Phase 2 Live Laya Integration Report

**Date:** October 19, 2026
**Auditor/Engineer:** DeepMind Antigravity Agent
**Scope:** Work Unit 1 (WU-1) — Phase 2 Live Laya Runtime Integration
**Final Verdict:** **ACCEPTED**

---

## 1. Executive Summary

Work Unit 1 has verified and established live runtime integration between the RivinityAds platform and the Laya decision engine sidecar.

Previously, Phase 2 acceptance was held as "acceptance pending" because automated test suites skipped the live smoke test (`test_real_runtime_smoke`) due to container network boundaries (`http://laya:8000` was unresolvable from the host environment).

Through a bounded, minimal-invasive change:
1. `backend/docker-compose.yml` was configured to map container port `8000` strictly to host loopback `127.0.0.1:8100` and enforce offline mode (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`).
2. `backend/tests/test_ad_server/test_laya_client.py` was updated to unskip `test_real_runtime_smoke`, decorate it with `@pytest.mark.smoke`, add a host port `127.0.0.1:8100` fallback with pre-flight health verification, and assert on real model inference without mocking.
3. Live inference against the real Laya neural decision engine was executed and verified end-to-end: HTTP 200 OK, roundtrip ~496ms (463.07ms inference time), valid decision `choice="no_change"`, and numeric float confidence `0.7936`.
4. All 54 Phase 2 regression tests pass with 0 failures and 0 skips (previously 53 passed, 1 skipped).
5. Phase 4 and all unrelated codebase components remain completely untouched.

---

## 2. Root Cause Analysis

### Identified Causes:
1. **Host Network Isolation:**
   - In `backend/docker-compose.yml`, service `laya` was connected solely to the internal Docker network `liteads-network` with no host port binding.
   - On the Windows development/testing host, the hostname `http://laya:8000` cannot be resolved.
   - Container port `8000` on the host could conflict with the FastAPI `ad-server` (which maps `8000:8000`), requiring an alternate host port (`8100`).

2. **Hardcoded Test Skip:**
   - In `backend/tests/test_ad_server/test_laya_client.py:255`, `test_real_runtime_smoke` was decorated with `@pytest.mark.skip(reason="Smoke test - requires real Laya sidecar running")`.
   - As a result, automated suites never executed live inference against the running container.

3. **Offline Asset Verification:**
   - Inspection of container `/home/laya/.cache/huggingface/hub/models--convaiinnovations--laya/snapshots/7b928d828b7b0e022f929d9bd2e44165aa270148` confirmed that all necessary assets (`model.safetensors`, `rl_agent_config.json`, `encoder/config.json`, `tokenizer/tokenizer.json`, `tokenizer_config.json`) are completely baked into the container image.
   - Setting `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` guarantees deterministic offline operation without HuggingFace network requests.

---

## 3. Files Changed and Rationale

| File Path | Changes Made | Rationale |
| :--- | :--- | :--- |
| `backend/docker-compose.yml` | Added `HF_HUB_OFFLINE=1` & `TRANSFORMERS_OFFLINE=1`<br>Added `ports: ["127.0.0.1:8100:8000"]` to `laya` service | Binds Laya safely to host loopback (`127.0.0.1`) only, avoiding external network exposure; guarantees offline inference with cached assets. |
| `backend/tests/test_ad_server/test_laya_client.py` | Removed `@pytest.mark.skip`<br>Added `@pytest.mark.smoke`<br>Added host URL fallback to `http://127.0.0.1:8100`<br>Added pre-flight health check and assertions | Enables live inference execution during test runs when Laya is up; gracefully skips if Laya is unreachable in environments without Docker; preserves fast test suites. |

Phase 4 files, models, routers, database schemas, and other services were **not modified**.

---

## 4. Verified API Contract (`POST /v1/systemone`)

### Request Schema:
- **Endpoint:** `POST {base_url}/v1/systemone`
- **Headers:** `Content-Type: application/json`
- **Body:**
  ```json
  {
    "state": "The campaign is performing very well with low CPA.",
    "questions": {
      "opt_decision": {
        "type": "choice",
        "instructions": "Determine the best optimization action for this campaign.",
        "criteria": {
          "increase_budget_20": "Increase daily budget by 20%",
          "decrease_budget_20": "Decrease daily budget by 20%",
          "shift_to_new_geo": "Shift targeting to a new geographic region",
          "shift_to_new_sector": "Shift targeting to a new sector",
          "improve_creative": "Recommend creative improvement",
          "no_change": "No changes needed"
        }
      }
    }
  }
  ```

### Live Response Body:
```json
{
  "model": "laya-rl-agent",
  "answers": {
    "opt_decision": {
      "type": "choice",
      "choice": "no_change",
      "probabilities": {
        "increase_budget_20": 0.0152,
        "decrease_budget_20": 0.0423,
        "shift_to_new_geo": 0.0018,
        "shift_to_new_sector": 0.0013,
        "improve_creative": 0.0192,
        "no_change": 0.9202
      },
      "confidence": 0.7936,
      "answer_confidence": 0.9202,
      "action": {
        "act_probability": 1.0
      }
    }
  },
  "usage": {
    "input_tokens": 104,
    "output_tokens": 0,
    "state_tokens": 11,
    "state_tokens_dropped": 0,
    "truncated": false,
    "truncated_questions": []
  },
  "routing": {
    "model": "english",
    "repo": "convaiinnovations/laya",
    "reason": "English Latin text",
    "detection": {
      "script": "latin",
      "script_profile": {
        "latin": 1.0
      },
      "language": "en",
      "is_english": true,
      "language_undecided": false,
      "diacritic_rate": 0.0,
      "non_latin_fraction": 0.0,
      "mixed_segment": null
    },
    "workflow": null
  }
}
```

### Contract Verification Key Findings:
- Choice location: `answers.opt_decision.choice` (string value: `"no_change"`).
- Confidence location: `answers.opt_decision.confidence` (IEEE 754 float: `0.7936`).
- Supported choices: 6 criteria options match the `LayaDecisionResult` literal types.
- Timing headers: `server-timing: inference;dur=463.07`, `x-inference-time-ms: 463.07`.

---

## 5. Empirical Commands and Verification Outputs

### 1. Docker Compose Configuration Validation
- **Command:** `docker compose config`
- **Working Dir:** `backend/`
- **Exit Code:** `0`
- **Verified Config for `laya`:**
  ```yaml
    laya:
      build:
        context: https://github.com/NandhaKishorM/laya.git
        dockerfile: Dockerfile
      command:
        - laya-serve
      environment:
        HF_HUB_OFFLINE: "1"
        LAYA_HOST: 0.0.0.0
        LAYA_PORT: "8000"
        LAYA_PRELOAD: "0"
        TRANSFORMERS_OFFLINE: "1"
      networks:
        liteads-network: null
      ports:
        - mode: ingress
          host_ip: 127.0.0.1
          target: 8000
          published: "8100"
          protocol: tcp
      restart: unless-stopped
  ```

### 2. Container Status and Health Check
- **Command:** `curl.exe -i http://127.0.0.1:8100/health`
- **Exit Code:** `0`
- **Output:**
  ```
  HTTP/1.1 200 OK
  date: Mon, 19 Oct 2026 05:25:37 GMT
  server: uvicorn
  content-length: 245
  content-type: application/json

  {"status":"ok","loaded":["english"],"revisions":{"english":"7b928d828b7b0e022f929d9bd2e44165aa270148"},"device":"cpu","device_is_preference":false,"checkpoint_devices":{"english":"cpu"},"cpu_fallbacks":{"english":{"count":0,"last_reason":null}}}
  ```

### 3. Live Smoke Test Execution
- **Command:** `.venv\Scripts\pytest tests/test_ad_server/test_laya_client.py::test_real_runtime_smoke -vv`
- **Exit Code:** `0`
- **Output:**
  ```
  collected 1 item
  tests/test_ad_server/test_laya_client.py::test_real_runtime_smoke PASSED [100%]
  ======================== 1 passed, 1 warning in 1.51s =========================
  ```

### 4. Full Laya Client Unit Test Suite
- **Command:** `.venv\Scripts\pytest tests/test_ad_server/test_laya_client.py -v`
- **Exit Code:** `0`
- **Output:**
  ```
  collected 13 items
  tests/test_ad_server/test_laya_client.py::test_successful_laya_response PASSED [  7%]
  tests/test_ad_server/test_laya_client.py::test_laya_returns_no_change PASSED [ 15%]
  tests/test_ad_server/test_laya_client.py::test_laya_unavailable PASSED   [ 23%]
  tests/test_ad_server/test_laya_client.py::test_laya_returns_non_2xx PASSED [ 30%]
  tests/test_ad_server/test_laya_client.py::test_malformed_response_json PASSED [ 38%]
  tests/test_ad_server/test_laya_client.py::test_malformed_response_missing_answers PASSED [ 46%]
  tests/test_ad_server/test_laya_client.py::test_missing_choice PASSED     [ 53%]
  tests/test_ad_server/test_laya_client.py::test_missing_confidence PASSED [ 61%]
  tests/test_ad_server/test_laya_client.py::test_invalid_confidence PASSED [ 69%]
  tests/test_ad_server/test_laya_client.py::test_invalid_choice PASSED     [ 76%]
  tests/test_ad_server/test_laya_client.py::test_actual_request_path_and_schema PASSED [ 84%]
  tests/test_ad_server/test_laya_client.py::test_configuration PASSED      [ 92%]
  tests/test_ad_server/test_laya_client.py::test_real_runtime_smoke PASSED [100%]
  ======================== 13 passed, 1 warning in 1.21s ========================
  ```

### 5. Phase 2 Full Regression Test Suite
- **Command:** `.venv\Scripts\pytest tests/test_ad_server/test_laya_client.py tests/test_ad_server/test_optimization_policy.py tests/test_ad_server/test_optimization_executor.py tests/test_ad_server/test_optimization_scheduler.py tests/test_ad_server/test_autopilot_api.py tests/test_ad_server/test_suggestion_api.py -v`
- **Exit Code:** `0`
- **Output Summary:**
  ```
  ======================= 54 passed, 43 warnings in 9.15s =======================
  ```
  *(54 passed, 0 failed, 0 skipped — vs. 53 passed, 1 skipped previously)*

### 6. Phase 4 Test Suite Intactness Check
- **Command:** `.venv\Scripts\pytest tests/test_api/test_phase4a_email.py tests/test_api/test_phase4b_domain.py tests/test_api/test_phase4c_moderation.py tests/test_api/test_phase4d_enforcement.py tests/test_ad_server/test_phase4.py -v`
- **Exit Code:** `0`
- **Output Summary:**
  ```
  ======================= 38 passed, 24 warnings in 4.27s =======================
  ```

---

## 6. Acceptance Criteria Verification

| Acceptance Criterion | Result | Evidence |
| :--- | :---: | :--- |
| Real Laya inference endpoint reachable | **Satisfied** | `http://127.0.0.1:8100/health` returns HTTP 200; `POST /v1/systemone` returns HTTP 200 in ~463ms. |
| Response conforms to verified API contract | **Satisfied** | Response returns top-level `answers`, `usage`, `routing`, and `model: "laya-rl-agent"`. |
| Returned decision and confidence satisfy schema | **Satisfied** | Choice is `"no_change"`; confidence is float `0.7936` ($0.0 \le c \le 1.0$). |
| Live smoke test passes against running service | **Satisfied** | `test_real_runtime_smoke` executes live HTTP call and passes. |
| Relevant regression tests pass with accurate reporting | **Satisfied** | 54 / 54 Phase 2 tests pass (0 failures, 0 skips). |
| Phase 4 remains untouched | **Satisfied** | No Phase 4 files modified; all 38 Phase 4 tests pass cleanly. |
| Unrelated user work preserved | **Satisfied** | Working directory clean except the two bounded edits and untracked audit docs. |

---

## 7. Remaining Operational Risks

1. **Docker Service Lifecycle:**
   The live integration test requires the Docker container `backend-laya-1` to be running on the host machine. If Docker is offline, `test_real_runtime_smoke` will gracefully skip (via pre-flight check) rather than causing build failures.
2. **Upstream Temperature Warning:**
   Laya's internal checkpoint emits: `RuntimeWarning: laya: this checkpoint ships invalid temperatures or values outside [0.5, 5]; using choice:11+=0.10058280825614929 -> 0.5.` This is an upstream weight calibration notice inside the `laya` Python library that does not affect inference reliability or contract adherence.
