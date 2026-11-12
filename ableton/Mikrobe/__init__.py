"""Maschine Mikro MK2 control surface for Ableton Live (works with mikrobe.py)."""
from __future__ import absolute_import, print_function, unicode_literals

from .Mikrobe import Mikrobe


def create_instance(c_instance):
    return Mikrobe(c_instance)
