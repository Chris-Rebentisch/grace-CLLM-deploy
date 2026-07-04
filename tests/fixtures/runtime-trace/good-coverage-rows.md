# Fixture — standalone paths + coverage rows (must pass lint)

## 2. Files Created and Edited

### 2.2 Created — Backend
| `src/ingestion/pipeline.py` | pull |
| `src/ingestion/adapter_base.py` | adapter |
| `config/voice_tone_config.yaml` | config |
| `tests/ingestion/test_models.py` | tests |

## 6. Build Steps / Checkpoints

### Step 3: Config load

**Runtime trace:** *(config + tests)*

| Step | Caller | Callee | File:anchor |
|------|--------|--------|-------------|
| 1 | startup | reads config via yaml.safe_load | `config/voice_tone_config.yaml` |
| 2 | parse_message | AdapterResult unwrap | `src/ingestion/adapter_base.py:57` |
| 3 | pipeline | run pull | `src/ingestion/pipeline.py:86` |
| 4 | test surface — models | validator assertions | `tests/ingestion/test_models.py` |

**Files:** `src/ingestion/pipeline.py`, `src/ingestion/adapter_base.py`, `config/voice_tone_config.yaml`, `tests/ingestion/test_models.py`

**Checkpoint:** *[CP3]*
