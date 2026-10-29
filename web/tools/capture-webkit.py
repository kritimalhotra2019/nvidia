#!/usr/bin/env python3
"""Headless captures of the pet with WebKitGTK (the closest Linux cousin of WKWebView).

Usage: xvfb-run -a python3 tools/capture-webkit.py [--out shots] [--size 320] [state ...]
Each state is rendered from dist-preview/index.html?bg=1&state=<state>; a ?white=1 and a plain
transparent variant are also captured for the first state so the outline reads on any desktop.
Extra query (e.g. props=twin, talking=1) can be given as state:query.
"""
import argparse, os, sys, time
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, GLib

STATES = ['idle', 'listening', 'thinking', 'analysing', 'approving', 'success', 'recording', 'error', 'sleeping']

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='shots')
    ap.add_argument('--size', type=int, default=320)
    ap.add_argument('--settle', type=float, default=1.4, help='seconds to let the animation settle before each shot')
    ap.add_argument('states', nargs='*')
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    page = os.path.abspath(os.path.join(here, '..', 'dist-preview', 'index.html'))
    if not os.path.exists(page):
        print('build the preview first: npx vite build --outDir dist-preview', file=sys.stderr); sys.exit(2)
    os.makedirs(a.out, exist_ok=True)
    jobs = []
    specs = a.states or STATES
    for i, spec in enumerate(specs):
        state, _, extra = spec.partition(':')
        q = f'bg=1&state={state}' + (('&' + extra) if extra else '')
        jobs.append((f'{spec.replace(":", "_").replace("=", "-").replace(",", "+")}.png', q))
        if i == 0 and not a.states:
            jobs.append((f'{state}_white.png', f'white=1&state={state}'))
            jobs.append((f'{state}_transparent.png', f'state={state}'))

    win = Gtk.OffscreenWindow()
    settings = WebKit2.Settings(enable_webgl=True, enable_developer_extras=True, allow_file_access_from_file_urls=True, allow_universal_access_from_file_urls=True)
    web = WebKit2.WebView(settings=settings)
    web.set_size_request(a.size, a.size)
    win.add(web)
    win.show_all()
    errors = []
    def on_console(view, msg):
        errors.append(str(msg))
    try:
        web.connect('console-message', lambda v, m: errors.append(str(m)))
    except TypeError:
        pass

    state = {'i': 0}
    def next_job():
        if state['i'] >= len(jobs):
            Gtk.main_quit(); return False
        name, q = jobs[state['i']]
        web.load_uri(f'file://{page}?{q}')
        GLib.timeout_add(int(a.settle * 1000), lambda: snap(name))
        return False
    def snap(name):
        def done(view, res):
            try:
                surf = view.get_snapshot_finish(res)
                surf.write_to_png(os.path.join(a.out, name))
                print('shot', name)
            except Exception as e:
                print('snapshot failed', name, e)
            state['i'] += 1
            GLib.idle_add(next_job)
        web.get_snapshot(WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.TRANSPARENT_BACKGROUND, None, done)
        return False
    GLib.idle_add(next_job)
    GLib.timeout_add(int(60000), lambda: (print('timeout'), Gtk.main_quit()))
    Gtk.main()
    if errors:
        print('console:', *errors[:10], sep='\n  ')

if __name__ == '__main__':
    main()
