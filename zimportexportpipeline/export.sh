#!/bin/bash
# Export data from Django models to JSON files
# Usage: ./export.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default Django management command
MANAGE_PY="python manage.py"

# Configuration
EXPORT_DIR="exports"
SCRIPT_NAME="export_data.py"

echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           DJANGO DATA EXPORT TOOL                       ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if we're in a Django project
if [ ! -f "manage.py" ]; then
    echo -e "${RED}Error: manage.py not found in current directory${NC}"
    echo "Please run this script from your Django project root"
    exit 1
fi

# Check if export script exists
if [ ! -f "$SCRIPT_NAME" ]; then
    echo -e "${RED}Error: $SCRIPT_NAME not found${NC}"
    echo "Please ensure export_data.py is in the current directory"
    exit 1
fi

# Create exports directory
echo -e "${YELLOW}Creating export directory...${NC}"
mkdir -p "$EXPORT_DIR"

# Check if directory is writable
if [ ! -w "$EXPORT_DIR" ]; then
    echo -e "${RED}Error: Cannot write to $EXPORT_DIR directory${NC}"
    exit 1
fi

# Check if there's existing data and ask for confirmation
if [ "$(ls -A $EXPORT_DIR 2>/dev/null)" ]; then
    echo -e "${YELLOW}Warning: $EXPORT_DIR already contains files${NC}"
    read -p "Do you want to continue and overwrite? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Export cancelled."
        exit 0
    fi
fi

echo ""
echo -e "${GREEN}Starting export...${NC}"
echo -e "${YELLOW}This may take a few moments depending on your data size${NC}"
echo ""

# Record start time
START_TIME=$(date +%s)

# Run the export
$MANAGE_PY shell < $SCRIPT_NAME

# Record end time
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo -e "${GREEN}Export completed successfully!${NC}"
echo ""

# Display export summary
if [ -f "$EXPORT_DIR/manifest.json" ]; then
    echo -e "${BLUE}Export Summary:${NC}"
    echo -e "${BLUE}─────────────────────────────────────────────────────────${NC}"
    
    # Parse and display manifest
    TOTAL_MODELS=$(grep -o '"model"' "$EXPORT_DIR/manifest.json" | wc -l)
    TOTAL_FILES=$(ls -1 "$EXPORT_DIR"/*.json 2>/dev/null | wc -l)
    MANIFEST_SIZE=$(du -h "$EXPORT_DIR/manifest.json" | cut -f1)
    TOTAL_SIZE=$(du -sh "$EXPORT_DIR" | cut -f1)
    
    echo -e "  ${GREEN}✓${NC} Location:       $EXPORT_DIR/"
    echo -e "  ${GREEN}✓${NC} Total Models:   $TOTAL_MODELS"
    echo -e "  ${GREEN}✓${NC} JSON Files:     $TOTAL_FILES"
    echo -e "  ${GREEN}✓${NC} Total Size:     $TOTAL_SIZE"
    echo -e "  ${GREEN}✓${NC} Duration:       ${DURATION}s"
    echo ""
    
    # List the exported files
    echo -e "${BLUE}Exported Files:${NC}"
    echo -e "${BLUE}─────────────────────────────────────────────────────────${NC}"
    ls -lh "$EXPORT_DIR"/*.json 2>/dev/null | awk '{printf "  %s (%s)\n", $9, $5}' | sed 's|exports/||'
    echo ""
    
    # Show manifest content
    echo -e "${BLUE}Manifest Preview:${NC}"
    echo -e "${BLUE}─────────────────────────────────────────────────────────${NC}"
    cat "$EXPORT_DIR/manifest.json" | python -m json.tool 2>/dev/null || cat "$EXPORT_DIR/manifest.json"
    echo ""
else
    echo -e "${YELLOW}Warning: Manifest file not found${NC}"
    echo "Files may have been exported but manifest generation failed"
fi

echo -e "${GREEN}✓ Export complete! Data is ready for import.${NC}"
echo ""
echo -e "To import this data, run: ${BLUE}./import.sh${NC}"

# Exit with success
exit 0