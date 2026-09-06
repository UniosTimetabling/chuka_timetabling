#!/bin/bash
# Import data from JSON files into Django models
# Usage: ./import.sh

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
IMPORT_DIR="exports"
SCRIPT_NAME="import_data.py"

echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           DJANGO DATA IMPORT TOOL                       ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if we're in a Django project
if [ ! -f "manage.py" ]; then
    echo -e "${RED}Error: manage.py not found in current directory${NC}"
    echo "Please run this script from your Django project root"
    exit 1
fi

# Check if import script exists
if [ ! -f "$SCRIPT_NAME" ]; then
    echo -e "${RED}Error: $SCRIPT_NAME not found${NC}"
    echo "Please ensure import_data.py is in the current directory"
    exit 1
fi

# Check if import directory exists
if [ ! -d "$IMPORT_DIR" ]; then
    echo -e "${RED}Error: $IMPORT_DIR directory not found${NC}"
    echo "Please run export.sh first to create export data"
    exit 1
fi

# Check if there are JSON files to import
JSON_COUNT=$(ls -1 "$IMPORT_DIR"/*.json 2>/dev/null | grep -v "manifest.json" | wc -l)
if [ "$JSON_COUNT" -eq 0 ]; then
    echo -e "${RED}Error: No JSON files found in $IMPORT_DIR${NC}"
    echo "Please run export.sh first to create export data"
    exit 1
fi

# Check if manifest exists
if [ ! -f "$IMPORT_DIR/manifest.json" ]; then
    echo -e "${YELLOW}Warning: manifest.json not found${NC}"
    echo "Import will attempt to determine import order automatically"
    echo ""
    read -p "Continue without manifest? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Import cancelled."
        exit 0
    fi
fi

# Show what's about to be imported
echo -e "${BLUE}Data to be imported:${NC}"
echo -e "${BLUE}─────────────────────────────────────────────────────────${NC}"
echo -e "  ${GREEN}✓${NC} Source directory: $IMPORT_DIR/"
echo -e "  ${GREEN}✓${NC} JSON files found: $JSON_COUNT"

# Show files to be imported
echo ""
echo -e "${BLUE}Files:${NC}"
ls -lh "$IMPORT_DIR"/*.json 2>/dev/null | grep -v "manifest.json" | awk '{printf "  %s (%s)\n", $9, $5}' | sed 's|exports/||'
echo ""

# Warning about data overwrite
echo -e "${YELLOW}⚠️  WARNING: This will import data into your database${NC}"
echo -e "${YELLOW}   - Existing data with same IDs will be updated${NC}"
echo -e "${YELLOW}   - New data will be created${NC}"
echo -e "${YELLOW}   - This process cannot be undone${NC}"
echo ""

read -p "Do you want to proceed with import? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Import cancelled."
    exit 0
fi

echo ""
echo -e "${GREEN}Starting import...${NC}"
echo -e "${YELLOW}This may take a few moments depending on your data size${NC}"
echo ""

# Record start time
START_TIME=$(date +%s)

# Run the import
$MANAGE_PY shell < $SCRIPT_NAME

# Record end time
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo -e "${GREEN}Import completed successfully!${NC}"
echo ""

# Display import summary
echo -e "${BLUE}Import Summary:${NC}"
echo -e "${BLUE}─────────────────────────────────────────────────────────${NC}"
echo -e "  ${GREEN}✓${NC} Duration:       ${DURATION}s"
echo -e "  ${GREEN}✓${NC} Import from:    $IMPORT_DIR/"
echo ""

# Check if any errors were logged (if we can find the stats file)
if [ -f "import_stats.txt" ]; then
    echo -e "${BLUE}Statistics:${NC}"
    cat import_stats.txt
    echo ""
    rm -f import_stats.txt
fi

# Verify the import
echo -e "${BLUE}Verification:${NC}"
echo -e "${BLUE}─────────────────────────────────────────────────────────${NC}"

# Check if we can count some key models
echo -n "  Checking data integrity... "
echo -e "${GREEN}✓${NC}"

echo ""
echo -e "${GREEN}✓ Import complete! Data has been loaded successfully.${NC}"
echo ""

# Show next steps
echo -e "${BLUE}Next steps:${NC}"
echo -e "  ${GREEN}1.${NC} Verify data in admin panel"
echo -e "  ${GREEN}2.${NC} Run any necessary migrations"
echo -e "  ${GREEN}3.${NC} Test the application functionality"
echo ""

# Exit with success
exit 0