# fix_ecotourism.py
# Run with: python manage.py shell < fix_ecotourism.py

from django.db import transaction
from django.db.models import Q
from department_management.models import Department
from program_management.models import Program
from course_allocation.models import CourseAllocation
import sys

def main():
    print("=" * 70)
    print(" Certificate in Hospitality and Tourism Management - CourseAllocation Fix")
    print("=" * 70)
    print()
    
    try:
        # ── 1. Find departments ───────────────────────────────────────────────────
        print("📂 Looking up departments...")
        
        # Get Environmental Science department
        env_dept = Department.objects.filter(
            Q(name__iexact="Environmental Science & Resources Development") |
            Q(name__icontains="Environmental")
        ).first()
        
        if not env_dept:
            print("  ✗ Environmental Science department not found!")
            print("\nAvailable departments:")
            for dept in Department.objects.all().order_by('name')[:20]:
                print(f"    - {dept.name}")
            return False
        
        print(f"  ✓ Environmental Science: {env_dept.name} (ID: {env_dept.id})")
        
        # Get Management Science department
        mgmt_dept = Department.objects.filter(
            Q(name__iexact="Management Science") |
            Q(name__icontains="Management Science")
        ).first()
        
        if mgmt_dept:
            print(f"  ✓ Management Science: {mgmt_dept.name} (ID: {mgmt_dept.id})")
        else:
            print("  ⚠ Management Science department not found!")
        
        print()
        
        # ── 2. Find the Ecotourism program ──────────────────────────────────────
        print("📚 Looking up Certificate in Hospitality and Tourism Management program...")
        
        ecotourism = Program.objects.filter(
            Q(name="Certificate in Hospitality and Tourism Management") |
            Q(name__icontains="Ecotourism")
        ).first()
        
        if not ecotourism:
            print("  ✗ Certificate in Hospitality and Tourism Management program not found!")
            print("\nAvailable programs containing 'Ecotourism':")
            for prog in Program.objects.filter(name__icontains="Ecotourism"):
                print(f"    - {prog.name} (ID: {prog.id})")
            return False
        
        print(f"  ✓ Found: {ecotourism.name} (ID: {ecotourism.id})")
        print()
        
        # ── 3. Find all allocations for this program ─────────────────────────────
        print("🔍 Finding CourseAllocations...")
        
        total_allocations = CourseAllocation.objects.filter(program=ecotourism)
        total_count = total_allocations.count()
        
        if total_count == 0:
            print(f"  ⚠ No CourseAllocations found for {ecotourism.name}")
            return True
        
        print(f"  Found {total_count} total CourseAllocation(s)")
        
        # Count by department
        dept_stats = {}
        for alloc in total_allocations:
            dept_name = alloc.department.name if alloc.department else "None"
            dept_stats[dept_name] = dept_stats.get(dept_name, 0) + 1
        
        print("\n  Current department distribution:")
        for dept_name, count in sorted(dept_stats.items()):
            print(f"    - {dept_name}: {count} allocation(s)")
        
        print()
        
        # ── 4. Find allocations that need fixing ────────────────────────────────
        if mgmt_dept:
            allocations_to_fix = total_allocations.filter(department=mgmt_dept)
            fix_count = allocations_to_fix.count()
        else:
            # If Management Science not found, update all to Environmental
            allocations_to_fix = total_allocations
            fix_count = total_count
        
        if fix_count == 0:
            print(f"  ℹ️ No allocations with Management Science department found.")
            
            # Check if they're already in Environmental Science
            already_correct = total_allocations.filter(department=env_dept).count()
            if already_correct == total_count:
                print(f"  ✓ All {total_count} allocations are already in Environmental Science!")
                return True
            else:
                print(f"\n  Would you like to update ALL {total_count} allocations to Environmental Science?")
                print(f"  (Currently {already_correct} are already correct, {total_count - already_correct} are in other departments)")
                
                response = input("Continue? (yes/no): ").strip().lower()
                if response not in ['yes', 'y']:
                    print("❌ Operation cancelled.")
                    return False
                
                allocations_to_fix = total_allocations
                fix_count = total_count
        
        # ── 5. Show allocations to fix ──────────────────────────────────────────
        print(f"\n📋 Found {fix_count} allocation(s) to fix:")
        print("  " + "-" * 66)
        
        # Show first 10 allocations
        for idx, alloc in enumerate(allocations_to_fix[:10], 1):
            dept_name = alloc.department.name if alloc.department else "None"
            group_name = alloc.student_group.name if alloc.student_group else "Shared"
            print(f"  {idx}. {alloc.course_code} - {alloc.course_name[:40]}")
            print(f"     Department: {dept_name} → Group: {group_name}")
            print()
        
        if fix_count > 10:
            print(f"  ... and {fix_count - 10} more allocation(s)")
        
        print("  " + "-" * 66)
        print()
        
        # ── 6. Ask for confirmation ─────────────────────────────────────────────
        dept_name = mgmt_dept.name if mgmt_dept else 'current department'
        print(f"⚠️  This will update {fix_count} allocation(s) from")
        print(f"   {dept_name} to {env_dept.name}")
        print()
        
        # Use raw_input for Python 2 compatibility, input for Python 3
        try:
            response = raw_input("Continue? (yes/no): ").strip().lower()
        except NameError:
            response = input("Continue? (yes/no): ").strip().lower()
        
        if response not in ['yes', 'y']:
            print("❌ Operation cancelled.")
            return False
        
        # ── 7. Perform the update ───────────────────────────────────────────────
        print("\n🔄 Updating allocations...")
        
        with transaction.atomic():
            # Update department
            updated = allocations_to_fix.update(department=env_dept)
            print(f"  ✓ Updated {updated} allocation(s) department → {env_dept.name}")
            
            # Update origin_department if it was Management Science
            if mgmt_dept:
                origin_updates = allocations_to_fix.filter(origin_department=mgmt_dept)
                origin_count = origin_updates.count()
                if origin_count > 0:
                    origin_updates.update(origin_department=env_dept)
                    print(f"  ✓ Updated {origin_count} origin_department reference(s) → {env_dept.name}")
        
        print()
        
        # ── 8. Verification ──────────────────────────────────────────────────────
        print("✅ Verifying updates...")
        
        # Count allocations now in Environmental Science
        new_count = CourseAllocation.objects.filter(
            program=ecotourism,
            department=env_dept
        ).count()
        
        print(f"  ✓ {new_count} allocation(s) now in {env_dept.name}")
        
        # Check if any still in Management Science
        if mgmt_dept:
            remaining = CourseAllocation.objects.filter(
                program=ecotourism,
                department=mgmt_dept
            ).count()
            
            if remaining == 0:
                print("  ✓ All allocations successfully updated!")
            else:
                print(f"  ⚠ {remaining} allocation(s) still in {mgmt_dept.name}")
        
        print()
        print("=" * 70)
        print("✅ Fix completed successfully!")
        print("=" * 70)
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

# ── Run the script ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)