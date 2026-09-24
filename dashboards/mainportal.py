from django.shortcuts import render
def mainportal(request):
    return render(request,'dashboard/main_portal.html')