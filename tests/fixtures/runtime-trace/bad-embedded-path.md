# Fixture — embedded path in backtick (must fail lint)

## 2. Files Created and Edited

### 2.2 Created — Backend
| `src/ingestion/pipeline.py` | pull |
| `config/voice_tone_config.yaml` | config |

## 6. Build Steps / Checkpoints

### Step 3: Config load

**Runtime trace:** *(config)*

| Step | Caller | Callee | File:anchor |
|------|--------|--------|-------------|
| 1 | startup | `yaml.safe_load(config/voice_tone_config.yaml)` | `src/ingestion/pipeline.py:1` |
| 2 | validate | Model.validate | `src/ingestion/pipeline.py:2` |
| 3 | extra | hop | `src/ingestion/pipeline.py:3` |

Uses AdapterResult.

**Files:** `src/ingestion/pipeline.py`, `config/voice_tone_config.yaml`

**Checkpoint:** *[CP3]*
