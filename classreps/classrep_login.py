from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.utils.crypto import get_random_string
from program_management.models import  Program
from .models import ClassRep

def generate_unique_reg_no(base_code: str, max_length: int = 20) -> str:
    """Generate a unique reg_no that doesn’t already exist in ClassRep."""
    for _ in range(20):  # retry up to 20 times just in case
        random_suffix = get_random_string(3).upper()
        reg_no = f"REP-{base_code}{random_suffix}"[:max_length]
        if not ClassRep.objects.filter(reg_no=reg_no).exists():
            return reg_no
    # fallback (extremely rare)
    return f"REP-{get_random_string(6).upper()}"[:max_length]


def classrep_login(request):
    """
    Class Rep login + credential reset page.
    Each Program automatically has a ClassRep account created.
    """
    with transaction.atomic():
        for program in Program.objects.all():
            # Skip if already linked to a ClassRep
            if ClassRep.objects.filter(program=program).exists():
                continue

            # Build short code from program name (first 3 initials)
            base_code = "".join(word[0].upper() for word in program.name.split()[:3])

            reg_no = generate_unique_reg_no(base_code, max_length=20)

            normalized = program.name.replace(" ", "").lower()
            username = normalized[:30]  # limit username length safely

            # Ensure no username duplicates
            if ClassRep.objects.filter(username=username).exists():
                username = f"{username}{get_random_string(2)}"

            ClassRep.objects.create(
                full_name=program.name,
                reg_no=reg_no,
                username=username,
                email=f"{username}@classrep.com",
                password=normalized,  # default = normalized
                program=program,
                active=True,
            )

    # --- Step 2: Handle reset credentials ---
    if request.method == "POST" and "reset" in request.POST:
        program_id = request.POST.get("program_id")
        if not program_id:
            messages.error(request, "Please select a program to reset credentials.")
        else:
            prog = get_object_or_404(Program, id=program_id)
            rep = get_object_or_404(ClassRep, program=prog)
            normalized = prog.name.replace(" ", "").lower()
            rep.username = normalized
            rep.password = normalized
            rep.email = f"{normalized}@classrep.com"
            rep.save()
            messages.success(
                request,
                f"Credentials reset! Username and password are now '{normalized}' for {prog.name}."
            )

    # --- Step 3: Handle login request ---
    elif request.method == "POST" and "login" in request.POST:
        username = request.POST.get("username", "").strip().lower()
        password = request.POST.get("password", "").strip()

        try:
            rep = ClassRep.objects.get(username=username)
            if rep.password == password:
                if not rep.active:
                    messages.error(request, "Your account is inactive. Contact the admin.")
                else:
                    request.session["classrep_id"] = rep.id
                    request.session["classrep_username"] = rep.username
                    return redirect("classrep_dashboard")
            else:
                messages.error(request, "Invalid password.")
        except ClassRep.DoesNotExist:
            messages.error(request, "No ClassRep account found with that username.")

    programs = Program.objects.all()
    return render(request, "classrep_login.html", {"programs": programs})