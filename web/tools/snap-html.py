#!/usr/bin/env python3
"""Render any local HTML file to PNG with WebKitGTK (dev box only): snap-html.py file.html out.png [width] [height]"""
import os, sys
import gi
gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, GLib

src, out = os.path.abspath(sys.argv[1]), sys.argv[2]
w = int(sys.argv[3]) if len(sys.argv) > 3 else 900
h = int(sys.argv[4]) if len(sys.argv) > 4 else 1400
win = Gtk.OffscreenWindow()
web = WebKit2.WebView(settings=WebKit2.Settings(enable_webgl=True, allow_file_access_from_file_urls=True))
web.set_size_request(w, h); win.add(web); win.show_all()
def done(view, res):
    try:
        view.get_snapshot_finish(res).write_to_png(out); print('wrote', out)
    except Exception as e:
        print('failed', e)
    Gtk.main_quit()
def snap():
    web.get_snapshot(WebKit2.SnapshotRegion.FULL_DOCUMENT, WebKit2.SnapshotOptions.NONE, None, done); return False
web.load_uri('file://' + src)
GLib.timeout_add(1500, snap)
GLib.timeout_add(20000, Gtk.main_quit)
Gtk.main()
