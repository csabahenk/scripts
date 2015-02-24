# -*- coding: utf-8 -*-
#
# Copyright (c) 2011 by Richard A Hofer (javagamer) <atomic.quark@gmail.com>
#
# fileaway.py - A simple autoaway script for Weechat which monitors a file,
# allowing it to easily connect to external things (such as xscreensaver)
# 
# The code from screen_away.py and auto_away.py were heavily consulted in the
# writing of this script
# ---------------------------------------------------------------------------
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>. 
#
# -------------------------------------------------------------------------
# Purpose -
# I wrote this auto-away script so that it could easily be hooked into other
# things to deterine whether or not I was there rather than just watching for
# keyboard input in Weechat.  In my case I wanted to be able to have weechat in
# a tmux session on a server, yet still go away when I lock the screen on my
# desktop.
#
# Below is a sample shellscript which watches xscreensaver and sets away when
# it is locked, and available when unlocked
# While this only one way this script can be used, this is why I wrote it
#
# #!/bin/sh
# 
# # Read xscreensaver's state
# xscreensaver-command -watch|
# while read STATUS; do
#   case "$STATUS" in
#     LOCK*)
#       rm ~/.available
#       ;;
#     UNBLANK*)
#       touch ~/.available
#       ;;
#   esac
# done
#
# Alternative for xset:
#
# #!/bin/bash
#
# xset -display ":0" q|grep "Monitor"|awk ' { print $3 $4 } '|
# while read MONITOR; do
#    case "$MONITOR" in
#      Off)
#        rm ~/.available
#        ;;
#      On)
#        touch ~/.available
#        ;;
#    esac
# done
# ------------------------------------------------------------------------
# Changelog:
# Version 1.0 released - March 27, 2011
#  -Initial release
# Version 1.0.1 released - March 31, 2011
#  -Handles improper commands
# Version 1.0.2 release - Jun 15, 2011
#  -Added alternative for xset users (credit: sherpa9 at irc.freenode.net)

try:
  import weechat as w

  import errno
  import os
  import re
  import time

except Exception:
  print "This script must be run under WeeChat."
  print "Get WeeChat now at: http://www.wwchat.org/"
  quit()

SCRIPT_NAME     = "fileaway"
SCRIPT_AUTHOR   = "javagamer"
SCRIPT_VERSION  = "1.0.2"
SCRIPT_LICENSE  = "GPL3"
SCRIPT_DESC     = "Set away status based on presence of a file"
debug           = 0
TIMER           = None
infolist_accessors = {
  'i': 'infolist_integer',
  's': 'infolist_string',
  't': 'infolist_time',
  #'b': 'infolist_buffer',
  'p': 'infolist_pointer'
}

away = None

settings = {
  'filepath':   './.available',
  'awaymessage':'Away',
  'expiry':     '0',
  'interval':   '20', # How often to check for inactivity (in seconds)
  'status':     '0',
}

def set_back(overridable_messages):
  '''Removes away status for servers where one of the overridable_messages is set'''
  global away
  if(away == False): return # No need to come back again
  serverlist = w.infolist_get('irc_server','','')
  if serverlist:
    buffers = []
    while w.infolist_next(serverlist):
      if w.infolist_string(serverlist, 'away_message') in overridable_messages:
        ptr = w.infolist_pointer(serverlist, 'buffer')
        if ptr:
          buffers.append(ptr)
    w.infolist_free(serverlist)
    for buffer in buffers:
      w.command(buffer, "/away")
  away = False

def set_away(message, overridable_messages=[]):
  '''Sets away status, but respectfully (so it doesn't change already set statuses'''
  global away
  if(away == True): return # No need to go away again (this prevents some repeated messages)
  if(debug): w.prnt('', "Setting away to %s" % message)
  serverlist = w.infolist_get('irc_server','','')
  if serverlist:
    buffers = []
    while w.infolist_next(serverlist):
      if w.infolist_integer(serverlist, 'is_away') == 0:
        if(debug): w.prnt('', "Not away on %s" % w.infolist_string(serverlist, 'name'))
        ptr = w.infolist_pointer(serverlist, 'buffer')
        if ptr:
          buffers.append(ptr)
      elif w.infolist_string(serverlist, 'away_message') in overridable_messages:
        if(debug): w.prnt('', "%s is in %s" % (w.infolist_string(serverlist, 'away_message'), repr(overridable_messages)))
        buffers.append(w.infolist_pointer(serverlist, 'buffer'))
    w.infolist_free(serverlist)
    if(debug): w.prnt('', repr(buffers))
    for buffer in buffers:
      w.command(buffer, "/away %s" % message)
  away = True

def fileaway_cb(data, buffer, args):
  response = {  'enable'  : lambda args: w.config_set_plugin('status', '1') and check_timer(),
                'disable' : lambda args: w.config_set_plugin('status', '0') and check_timer(),
                'check'   : check,
                'file'    : lambda filepath: w.config_set_plugin('filepath', filepath),
                'interval': lambda interval: w.config_set_plugin('interval', interval),
                'expiry'  : lambda expiry: w.config_set_plugin('expiry', expiry),
                'msg'     : lambda status: w.config_set_plugin('awaymessage', status),
            }
  if args:
    words = args.strip().partition(' ')
    if words[0] in response:
      response[words[0]](words[2])
    else:
      w.prnt('', "Fileaway error: %s not a recognized command.  Try /help fileaway" % words[0])
  w.prnt('', "fileaway: enabled: %s interval: %s expiry: %s away message: \"%s\" filepath: %s" %
    (w.config_get_plugin('status'), w.config_get_plugin('interval'), w.config_get_plugin('expiry'),
    w.config_get_plugin('awaymessage'), w.config_get_plugin('filepath')))
  return w.WEECHAT_RC_OK

def auto_check(data, remaining_calls):
  '''Callback from timer'''
  check(0)
  return w.WEECHAT_RC_OK

def relays():
  relays = []
  relaylist = w.infolist_get('relay','','')
  if relaylist:
    try:
      i = 0
      while w.infolist_next(relaylist):
        relays.append(w.infolist_string(relaylist, 'desc'))
        if debug:
          wfs = w.infolist_fields(relaylist)
          wf = [ tuple(x.split(":")) for x in wfs.split(",") ]
          for t, f in wf:
            v = getattr(w, infolist_accessors[t])(relaylist, f)
            w.prnt('', 'relay %d: %s(%s) %s' % (i, f, t, v))
        i += 1
    finally:
      w.infolist_free(relaylist)
  return relays

def check(args):
  '''Check for existance of file and set away if it isn't there'''
  available = True
  drx = None
  try:
    filepath = w.config_get_plugin('filepath')
    if debug:
      w.prnt('', "%s %s" % (os.getcwd(), filepath))
    st = os.stat(filepath)
    ctim = st.st_ctime
    with open(filepath) as f:
      content = f.read().strip()
      if content:
        # the content of the file is interpreted
        # as a regex that specifies those relay
        # clients whose presence is represented
        # by the file (referred to in sequel
        # as "managed relays")
        drx = re.compile(content)
  except OSError as ex:
    if ex.errno == errno.ENOENT:
      available = False
    else:
      raise
  if debug:
    w.prnt('', drx.pattern)
  expiry = int(w.config_get_plugin('expiry'))
  if available and expiry > 0:
     available = time.time() < ctim + expiry
  if drx is not None:
    relaystats = {'managed':0, 'freerunning':0}
    for r in relays():
      if drx.search(r):
        key = 'managed'
      else:
        key = 'freerunning'
      relaystats[key] += 1
    # - we evaluate presence in terms of relay clients, ie. if there is none, we decree away
    # - connection from a freerunning (non-managed) relay always means presence
    # - if only managed relays are connected, we judge by the age of the control file, ie.
    #   we decree presence if it was updated within expiry
    available = relaystats['freerunning'] or (available and relaystats['managed'])
  if debug:
    w.prnt('', 'rstat %s time %f ctim %d + expiry %d = %d available %s' % (`relaystats`, time.time(), ctim, expiry, ctim + expiry, `available`))
  if available:
    set_back([w.config_get_plugin('awaymessage')])
  else:
    set_away(w.config_get_plugin('awaymessage'), [])

def check_timer():
  '''Sets or unsets the timer based on whether or not the plugin is enabled'''
  global TIMER
  if TIMER:
      w.unhook(TIMER)
  if w.config_get_plugin('status') == '1':
    TIMER = w.hook_timer(int(w.config_get_plugin('interval')) * 1000, 0, 0, "auto_check", "")
    w.prnt('', "fileaway timer is running.")

if __name__ == "__main__":
  if w.register(SCRIPT_NAME, SCRIPT_AUTHOR, SCRIPT_VERSION, SCRIPT_LICENSE, SCRIPT_DESC, "", ""):
    for option, default_value in settings.iteritems():
      if not w.config_is_set_plugin(option):
        w.config_set_plugin(option, default_value)

    w.hook_command("fileaway", 
    "Set away status based on presense or absense of a file.",
    "check, msg [status], interval [time], file [filepath], or enable|disable",
    "check - manually checks for file rather than waiting for interval.\n"
    "msg [status] - sets the away message.\n"
    "expiry [time] - sets the time since last modification of file to accept.\n"
    "interval [time] - sets the interval to check for the file.\n"
    "file [filepath] - sets the file to be watched.\n"
    "enable|disable - enables or disables plugin.\n",
    "check"
    " || msg"
    " || interval"
    " || expiry"
    " || file %(filename)"
    " || enable"
    " || disable",
    "fileaway_cb", "")
    check_timer()
    if(w.config_get_plugin('status') == '0'):
      w.prnt('', "fileaway is currently disabled.  Type /fileaway enable to enable it.")
    else:
      w.prnt('', "fileaway is currently enabled.  Type /fileaway disable to disable it.")
