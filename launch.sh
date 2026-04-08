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
# Check for existing projects that can be resumed
# ───────────────────────────────────────────────────────────────
RESUMABLE=()
RESUMABLE_DISPLAY=()
for proj_dir in "$SCRIPT_DIR"/projects/*/; do
    [[ "$(basename "$proj_dir")" == "_template" ]] && continue
    manifest="$proj_dir/project_manifest.json"
    [[ ! -f "$manifest" ]] && continue

    # Find first incomplete phase
    next_phase=$(python3 -c "
import json, sys
m = json.loads(open('$manifest').read())
phases = m.get('phases', {})
for i in range(7):
    if phases.get(f'phase_{i}', {}).get('status') != 'complete':
        print(i); sys.exit()
print(7)
" 2>/dev/null)

    if [[ "$next_phase" -lt 7 ]]; then
        proj_name=$(python3 -c "import json; print(json.loads(open('$manifest').read()).get('projectName','?'))" 2>/dev/null)
        proj_id=$(basename "$proj_dir")
        RESUMABLE+=("$proj_id")
        RESUMABLE_DISPLAY+=("$proj_name  →  Phase $next_phase (${PHASE_NAMES[$next_phase]})")
    fi
done

MODE="new"
PROJECT_ID=""

if [[ ${#RESUMABLE[@]} -gt 0 ]]; then
    echo -e "${BOLD}Existing projects with progress:${RESET}"
    for i in "${!RESUMABLE[@]}"; do
        echo -e "  ${GREEN}$((i+1)))${RESET} ${RESUMABLE_DISPLAY[$i]}"
    done
    echo -e "  ${CYAN}N)${RESET} New project"
    echo ""
    read -rp "$(echo -e "${BOLD}Resume or New? [1-${#RESUMABLE[@]}/N]:${RESET} ")" PICK
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

    # Strip surrounding quotes if drag-dropped from file manager
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

    # ── 4. Stickiness (creative freedom) ──
    echo ""
    echo -e "${BOLD}Stickiness (creative freedom):${RESET}"
    echo "  1 = Reformat          2 = Remaster"
    echo "  3 = Expand            4 = Reimagine"
    echo "  5 = Create"
    read -rp "$(echo -e "${BOLD}Stickiness${RESET} [3]: ")" STICKINESS
    STICKINESS="${STICKINESS:-3}"

    # ── 5. Size ──
    echo ""
    echo -e "${BOLD}Project size:${RESET}"
    echo "  1) short       (10-20 frames)"
    echo "  2) short_film  (50-125 frames)"
    echo "  3) televised   (200-300 frames)"
    echo "  4) feature     (750-1250 frames)"
    read -rp "$(echo -e "${BOLD}Size${RESET} [1]: ")" SIZE_CHOICE
    SIZE_CHOICE="${SIZE_CHOICE:-1}"
    case "$SIZE_CHOICE" in
        1) SIZE="short" ;;
        2) SIZE="short_film" ;;
        3) SIZE="televised" ;;
        4) SIZE="feature" ;;
        *) echo -e "${RED}Invalid choice. Pick 1-4.${RESET}"; exit 1 ;;
    esac

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
    echo -e "${BOLD}  Name:${RESET}        $PROJECT_NAME"
    echo -e "${BOLD}  Source:${RESET}      $SOURCE_PATH"
    echo -e "${BOLD}  Stickiness:${RESET}  $STICKINESS"
    echo -e "${BOLD}  Size:${RESET}        $SIZE"
    echo -e "${BOLD}  Style:${RESET}       $MEDIA_STYLE"
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
        SEED_FILE=$(find "$SOURCE_PATH" -maxdepth 1 -type f \( -name '*.md' -o -name '*.txt' \) | head -1)
    fi

    # ── Create project ──
    echo -e "${GREEN}▸ Creating project...${RESET}"
    SEED_ARGS=()
    if [[ -n "$SEED_FILE" ]]; then
        SEED_ARGS=(--seed "$SEED_FILE")
    fi

    python3 create_project.py \
        --name "$PROJECT_NAME" \
        --id "$PROJECT_ID" \
        --stickiness "$STICKINESS" \
        --size "$SIZE" \
        --media-style "$MEDIA_STYLE" \
        "${SEED_ARGS[@]+"${SEED_ARGS[@]}"}"

    PROJECT_SOURCE_DIR="$SCRIPT_DIR/projects/$PROJECT_ID/source_files"

    # ── Copy source files ──
    echo -e "${GREEN}▸ Copying source files...${RESET}"
    if [[ -f "$SOURCE_PATH" ]]; then
        cp -v "$SOURCE_PATH" "$PROJECT_SOURCE_DIR/"
    elif [[ -d "$SOURCE_PATH" ]]; then
        cp -rv "$SOURCE_PATH"/. "$PROJECT_SOURCE_DIR/"
    fi

    # Update onboarding_config.json sourceFiles array with actual files
    SOURCE_LIST=$(find "$PROJECT_SOURCE_DIR" -maxdepth 1 -type f ! -name 'onboarding_config.json' -printf 'source_files/%f\n' | sort)
    python3 -c "
import json
cfg_path = '$PROJECT_SOURCE_DIR/onboarding_config.json'
cfg = json.loads(open(cfg_path).read())
cfg['sourceFiles'] = [l for l in '''$SOURCE_LIST'''.strip().split('\n') if l]
open(cfg_path, 'w').write(json.dumps(cfg, indent=2) + '\n')
"

    echo -e "${GREEN}▸ Source files in project:${RESET}"
    ls -1 "$PROJECT_SOURCE_DIR" | grep -v onboarding_config.json

    PIPELINE_FLAGS=""
fi

# ───────────────────────────────────────────────────────────────
# RESUME flow — just set the flag
# ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "resume" ]]; then
    PIPELINE_FLAGS="--resume"
    echo ""
    echo -e "${GREEN}▸ Resuming project: ${PROJECT_ID}${RESET}"
fi

# ───────────────────────────────────────────────────────────────
# Set up log tee (project dir exists at this point)
# ───────────────────────────────────────────────────────────────
LOGS_DIR="$SCRIPT_DIR/projects/$PROJECT_ID/logs"
mkdir -p "$LOGS_DIR"
LOG_FILE="$LOGS_DIR/launch_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee >(sed 's/\x1b\[[0-9;]*m//g' >> "$LOG_FILE")) 2>&1
echo -e "${DIM}Log file: ${LOG_FILE}${RESET}"

# ───────────────────────────────────────────────────────────────
# Run pipeline (unbuffered so python streams in real-time)
# ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}▸ Starting pipeline...${RESET}"
echo -e "${CYAN}────────────────────────────────────────────${RESET}"
PYTHONUNBUFFERED=1 python3 run_pipeline.py --project "$PROJECT_ID" ${PIPELINE_FLAGS:-}
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
