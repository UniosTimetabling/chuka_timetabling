from django import template

register = template.Library()

@register.filter
def replace(value, arg):
    """Replace substring in a string"""
    old, new = arg.split(',')
    return value.replace(old, new)