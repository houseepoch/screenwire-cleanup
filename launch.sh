#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────
# ScreenWire Pipeline — Single-Command Launcher
# New project or resume an existing one. All output streams to
# terminal AND a timestamped log file.
# ───────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ──
BOLD='\033[1m'
CYAN='\033[36m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
DIM='\033[2m'
RESET='\033[0m'

PHASE_NAMES=("Scaffold" "Narrative" "Graph" "Assets" "Composition" "Video" "Export")

echo -e "${BOLD}${CYAN}"
echo "╔═══════════════════════════════════════════╗"
echo "║       ScreenWire Pipeline Launcher        ║"
echo "╚═══════════════════════════════════════════╝"
echo -e "${RESET}"

# ───────────────────────────────────────────────────────────────
# Check for existing projects that can be resumed or healed
# ───────────────────────────────────────────────────────────────
RESUMABLE=()
RESUMABLE_DISPLAY=()
for proj_dir in "$SCRIPT_DIR"/projects/*/; do
    [[ ! -d "$proj_dir" ]] && continue
    [[ "$(basename "$proj_dir")" == "_template" ]] && continue
    manifest="$proj_dir/project_manifest.json"
    [[ ! -f "$manifest" ]] && continue

    resume_info="$(python3 - <<PY
import json
from pathlib import Path
import run_pipeline

project_dir = Path(r"$proj_dir").resolve()
manifest_path = project_dir / "project_manifest.json"
run_pipeline.PROJECT_DIR = project_dir
run_pipeline.MANIFEST_PATH = manifest_path

manifest = json.loads(manifest_path.read_text())
project_name = manifest.get("projectName", project_dir.name)
phases = manifest.get("phases", {})
next_phase = 7
reason = "complete"
issue = ""

for i in range(7):
    status = phases.get(f"phase_{i}", {}).get("status")
    if status != "complete":
        next_phase = i
        reason = status or "not_started"
        break
    reusable, issues = run_pipeline._phase_reuse_status(i, project_dir)
    if not reusable:
        next_phase = i
        reason = "heal"
        issue = issues[0] if issues else ""
        break

print(json.dumps({
    "project_name": project_name,
    "next_phase": next_phase,
    "reason": reason,
    "issue": issue,
}))
PY
)"

    next_phase="$(python3 - <<PY
import json
data = json.loads('''$resume_info''')
print(data["next_phase"])
PY
)"

    if [[ "$next_phase" -lt 7 ]]; then
        proj_id="$(basename "$proj_dir")"
        proj_name="$(python3 - <<PY
import json
data = json.loads('''$resume_info''')
print(data["project_name"])
PY
)"
        resume_reason="$(python3 - <<PY
import json
data = json.loads('''$resume_info''')
reason = data["reason"]
issue = data["issue"]
if reason == "heal" and issue:
    print(f"heal Phase {data['next_phase']} ({issue})")
elif reason == "ready":
    print(f"Phase {data['next_phase']} ready")
elif reason == "not_started":
    print(f"Phase {data['next_phase']} not started")
else:
    print(f"Phase {data['next_phase']} ({reason})")
PY
)"
        RESUMABLE+=("$proj_id")
        RESUMABLE_DISPLAY+=("$proj_name  →  $resume_reason")
    fi
done

MODE="new"
PROJECT_ID=""

if [[ ${#RESUMABLE[@]} -gt 0 ]]; then
    echo -e "${BOLD}Existing projects that can resume or heal:${RESET}"
    for i in "${!RESUMABLE[@]}"; do
        echo -e "  ${GREEN}$((i+1)))${RESET} ${RESUMABLE_DISPLAY[$i]}"
    done
    echo -e "  ${CYAN}N)${RESET} New project"
    echo ""
    read -rp "$(echo -e "${BOLD}Resume/heal or New? [1-${#RESUMABLE[@]}/N]:${RESET} ")" PICK
    PICK="${PICK:-N}"

    if [[ "$PICK" =~ ^[0-9]+$ ]] && [[ "$PICK" -ge 1 ]] && [[ "$PICK" -le ${#RESUMABLE[@]} ]]; then
        MODE="resume"
        PROJECT_ID="${RESUMABLE[$((PICK-1))]}"
    fi
fi

# ───────────────────────────────────────────────────────────────
# NEW PROJECT flow
# ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "new" ]]; then

    # ── 1. Project Name ──
    read -rp "$(echo -e "${BOLD}Project Name:${RESET} ")" PROJECT_NAME
    if [[ -z "$PROJECT_NAME" ]]; then
        echo -e "${RED}Error: Project name is required.${RESET}"
        exit 1
    fi

    # ── 2. Project ID (derived from name) ──
    PROJECT_ID="$(echo "$PROJECT_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '_' | tr -cd 'a-z0-9_')_$(date +%s)"

    # ── 3. Source file / folder ──
    echo ""
    echo -e "${YELLOW}Paste the path to your source file or folder."
    echo -e "All files will be copied into the project's source_files/.${RESET}"
    read -rp "$(echo -e "${BOLD}Source path:${RESET} ")" SOURCE_PATH

    SOURCE_PATH="${SOURCE_PATH%\"}"
    SOURCE_PATH="${SOURCE_PATH#\"}"
    SOURCE_PATH="${SOURCE_PATH%\'}"
    SOURCE_PATH="${SOURCE_PATH#\'}"

    if [[ -z "$SOURCE_PATH" ]]; then
        echo -e "${RED}Error: Source path is required.${RESET}"
        exit 1
    fi
    if [[ ! -e "$SOURCE_PATH" ]]; then
        echo -e "${RED}Error: Path not found: ${SOURCE_PATH}${RESET}"
        exit 1
    fi

    # ── 4. Creative Freedom ──
    echo ""
    echo -e "${BOLD}Creative freedom:${RESET}"
    echo "  1) strict      2) balanced"
    echo "  3) creative    4) unbounded"
    read -rp "$(echo -e "${BOLD}Creative freedom${RESET} [2]: ")" CREATIVE_CHOICE
    CREATIVE_CHOICE="${CREATIVE_CHOICE:-2}"
    case "$CREATIVE_CHOICE" in
        1) CREATIVE_FREEDOM="strict" ;;
        2) CREATIVE_FREEDOM="balanced" ;;
        3) CREATIVE_FREEDOM="creative" ;;
        4) CREATIVE_FREEDOM="unbounded" ;;
        *) echo -e "${RED}Invalid choice. Pick 1-4.${RESET}"; exit 1 ;;
    esac

    # ── 5. Frame Budget ──
    echo ""
    echo -e "${BOLD}Frame budget:${RESET}"
    echo "  Enter ${CYAN}auto${RESET} for uncapped coverage."
    echo "  Auto means: spare no expense, use as many frames as needed,"
    echo "  prioritize the richest project quality, and preserve full-story coverage."
    echo "  Or enter a positive integer frame cap (for example: 60, 180, 320)."
    read -rp "$(echo -e "${BOLD}Frame budget${RESET} [auto]: ")" FRAME_BUDGET
    FRAME_BUDGET="${FRAME_BUDGET:-auto}"
    if [[ ! "$FRAME_BUDGET" =~ ^auto$|^[1-9][0-9]*$ ]]; then
        echo -e "${RED}Invalid frame budget. Use 'auto' or a positive integer.${RESET}"
        exit 1
    fi

    # ── 6. Media style ──
    echo ""
    echo -e "${BOLD}Media style:${RESET}"
    echo "  1) New Digital Anime     2) Live Retro Grain"
    echo "  3) Chiaroscuro Live      4) Chiaroscuro 3d"
    echo "  5) Chiaroscuro Anime     6) Black Ink Anime"
    echo "  7) Live Soft Light       8) Live Clear"
    read -rp "$(echo -e "${BOLD}Media style${RESET} [8]: ")" MEDIA_CHOICE
    MEDIA_CHOICE="${MEDIA_CHOICE:-8}"
    case "$MEDIA_CHOICE" in
        1) MEDIA_STYLE="new_digital_anime" ;;
        2) MEDIA_STYLE="live_retro_grain" ;;
        3) MEDIA_STYLE="chiaroscuro_live" ;;
        4) MEDIA_STYLE="chiaroscuro_3d" ;;
        5) MEDIA_STYLE="chiaroscuro_anime" ;;
        6) MEDIA_STYLE="black_ink_anime" ;;
        7) MEDIA_STYLE="live_soft_light" ;;
        8) MEDIA_STYLE="live_clear" ;;
        *) echo -e "${RED}Invalid choice. Pick 1-8.${RESET}"; exit 1 ;;
    esac

    # ── Summary ──
    echo ""
    echo -e "${CYAN}────────────────────────────────────────────${RESET}"
    echo -e "${BOLD}  Name:${RESET}            $PROJECT_NAME"
    echo -e "${BOLD}  Source:${RESET}          $SOURCE_PATH"
    echo -e "${BOLD}  Creative Freedom:${RESET} $CREATIVE_FREEDOM"
    echo -e "${BOLD}  Frame Budget:${RESET}    $FRAME_BUDGET"
    echo -e "${BOLD}  Style:${RESET}           $MEDIA_STYLE"
    echo -e "${CYAN}────────────────────────────────────────────${RESET}"
    echo ""
    read -rp "$(echo -e "${BOLD}Proceed? [Y/n]:${RESET} ")" CONFIRM
    CONFIRM="${CONFIRM:-Y}"
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi

    # ── Detect seed file for --seed flag ──
    SEED_FILE=""
    if [[ -f "$SOURCE_PATH" ]]; then
        SEED_FILE="$SOURCE_PATH"
    elif [[ -d "$SOURCE_PATH" ]]; then
        SEED_FILE=$(find "$SOURCE_PATH" -maxdepth 1 -type f | head -1)
    fi

    # ── Create project ──
    echo -e "${GREEN}▸ Creating project...${RESET}"
    CREATE_CMD=(
        python3 create_project.py
        --name "$PROJECT_NAME"
        --id "$PROJECT_ID"
        --creative-freedom "$CREATIVE_FREEDOM"
        --frame-budget "$FRAME_BUDGET"
        --media-style "$MEDIA_STYLE"
    )
    if [[ -n "$SEED_FILE" ]]; then
        CREATE_CMD+=(--seed "$SEED_FILE")
    fi
    "${CREATE_CMD[@]}"

    PROJECT_SOURCE_DIR="$SCRIPT_DIR/projects/$PROJECT_ID/source_files"

    # ── Copy source files ──
    echo -e "${GREEN}▸ Copying source files...${RESET}"
    if [[ -f "$SOURCE_PATH" ]]; then
        cp -v "$SOURCE_PATH" "$PROJECT_SOURCE_DIR/"
    elif [[ -d "$SOURCE_PATH" ]]; then
        cp -rv "$SOURCE_PATH"/. "$PROJECT_SOURCE_DIR/"
    fi

    # Update onboarding_config.json sourceFiles array with actual files
    SOURCE_LIST="$(find "$PROJECT_SOURCE_DIR" -maxdepth 1 -type f ! -name 'onboarding_config.json' -printf 'source_files/%f\n' | sort)"
    python3 - <<PY
import json
from pathlib import Path

cfg_path = Path(r"$PROJECT_SOURCE_DIR") / "onboarding_config.json"
cfg = json.loads(cfg_path.read_text())
cfg["sourceFiles"] = [line for line in """$SOURCE_LIST""".strip().splitlines() if line]
cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
PY

    echo -e "${GREEN}▸ Source files in project:${RESET}"
    ls -1 "$PROJECT_SOURCE_DIR" | grep -v onboarding_config.json || true

    PIPELINE_FLAGS=""
fi

# ───────────────────────────────────────────────────────────────
# RESUME flow — just set the flag
# ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "resume" ]]; then
    PIPELINE_FLAGS="--resume"
    echo ""
    echo -e "${GREEN}▸ Resuming/healing project: ${PROJECT_ID}${RESET}"
fi

# ───────────────────────────────────────────────────────────────
# Set up log tee (project dir exists at this point)
# ───────────────────────────────────────────────────────────────
LOGS_DIR="$SCRIPT_DIR/projects/$PROJECT_ID/logs"
mkdir -p "$LOGS_DIR"
LOG_FILE="$LOGS_DIR/launch_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee >(sed 's/\x1b\[[0-9;]*m//g' >> "$LOG_FILE")) 2>&1
echo -e "${DIM}Log file: ${LOG_FILE}${RESET}"

LIVE_FLAG="--live"
if [[ "${SCREENWIRE_LIVE:-1}" == "0" ]]; then
    LIVE_FLAG=""
fi

# ───────────────────────────────────────────────────────────────
# Run pipeline (unbuffered so python streams in real-time)
# ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}▸ Starting pipeline...${RESET}"
echo -e "${CYAN}────────────────────────────────────────────${RESET}"
PYTHONUNBUFFERED=1 python3 run_pipeline.py --project "$PROJECT_ID" ${PIPELINE_FLAGS:-} ${LIVE_FLAG}
EXIT_CODE=$?

echo ""
echo -e "${CYAN}────────────────────────────────────────────${RESET}"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}Pipeline completed successfully.${RESET}"
else
    echo -e "${RED}${BOLD}Pipeline exited with code ${EXIT_CODE}.${RESET}"
fi
echo -e "${DIM}Full log: ${LOG_FILE}${RESET}"
exit $EXIT_CODE
