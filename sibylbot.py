#!/usr/bin/env python
#
# XBMC JSON-RPC XMPP MUC bot

# built-ins
import sys,json,time,os,subprocess,logging,pickle,socket,random,re

# dependencies
import requests
import xmpp
from jabberbot import JabberBot,botcmd
from smbclient import SambaClient,SambaClientError
from lxml.html import fromstring

class SibylBot(JabberBot):
  """More details: https://github.com/TheSchwa/sibyl/wiki/Commands"""

################################################################################
# Setup                                                                        #
################################################################################

  def __init__(self,*args,**kwargs):
    """override to only answer direct msgs"""

    # required kwargs
    self.rpi_ip = kwargs.get('rpi_ip')

    # optional kwargs (must be deleted)
    self.nick_name = kwargs.get('nick_name','Sibyl')
    self.audio_dirs = kwargs.get('audio_dirs',[])
    self.video_dirs = kwargs.get('video_dirs',[])
    self.lib_file = kwargs.get('lib_file','sibyl_lib.pickle')
    self.max_matches = kwargs.get('max_matches',10)
    self.xbmc_user = kwargs.get('xbmc_user',None)
    self.xbmc_pass = kwargs.get('xbmc_pass',None)
    self.chat_ctrl = kwargs.get('chat_ctrl',False)
    self.bw_list = kwargs.get('bw_list',[])
    self.log_file = kwargs.get('log_file','/var/log/sibyl.log')
    self.bm_file = kwargs.get('bm_file','sibyl_bm.txt')
    self.note_file = kwargs.get('note_file','sibyl_note.txt')
    self.ping_freq = kwargs.get('ping_freq',None)
    self.link_echo = kwargs.get('link_echo', False)

    # validate args
    self.validate_args()

    # default bw_list behavior is to allow everything
    self.bw_list.insert(0,('w','*','*'))

    # configure logging
    logging.basicConfig(filename=self.log_file,format='%(asctime)-15s | %(message)s')
    self.log = logging.getLogger()

    # delete kwargs before calling super init
    words = (['rpi_ip','nick_name','audio_dirs','video_dirs','log_file',
        'lib_file','max_matches','xbmc_user','xbmc_pass','chat_ctrl',
        'bw_list','bm_file','note_file','link_echo'])
    for w in words:
      try:
        del kwargs[w]
      except KeyError:
        pass

    # create libraries
    if os.path.isfile(self.lib_file):
      self.library(None,'load')
    else:
      self.lib_last_rebuilt = time.asctime()
      self.lib_last_elapsed = 0
      self.lib_audio_dir = None
      self.lib_audio_file = None
      self.lib_video_dir = None
      self.lib_video_file = None
      self.library(None,'rebuild')

    # initialize bookmark dict and last played str for bookmarking
    if os.path.isfile(self.bm_file):
      self.bm_store = self.bm_parse()
    else:
      with open(self.bm_file,'w') as f:
        self.bm_store = {}
    self.last_played = None

    # initialize note list
    if os.path.isfile(self.note_file):
      self.notes = self.note_parse()
    else:
      with open(self.note_file,'w') as f:
        self.notes = []

    # variable to keep track of last executed command
    self.last_cmd = 'echo Nothing to redo'

    # call JabberBot init
    super(SibylBot,self).__init__(*args,**kwargs)

  def validate_args(self):
    """validate args to prevent errors popping up during run-time"""

    # type checking
    if not isinstance(self.nick_name,str):
      raise TypeError('param nick_name must be str')
    if not isinstance(self.audio_dirs,list):
      raise TypeError('param audio_dirs must be list')
    if not isinstance(self.video_dirs,list):
      raise TypeError('param video_dirs must be list')
    if not isinstance(self.log_file,str):
      raise TypeError('param log_file must be str')
    if not isinstance(self.lib_file,str):
      raise TypeError('param lib_file must be str')
    if not isinstance(self.bm_file,str):
      raise TypeError('param bm_file must be str')
    if not isinstance(self.note_file,str):
      raise TypeError('param not_file must be str')
    if not isinstance(self.max_matches,int):
      raise TypeError('param max_matches must be int')
    if not isinstance(self.chat_ctrl,bool):
      raise TypeError('param chat_ctrl must be bool')
    if not isinstance(self.bw_list,list):
      raise TypeError('param bw_list must be list')
    if not isinstance(self.link_echo,bool):
      raise TypeError('param link_echo must be bool')

    # these may also be None
    if self.xbmc_user is not None and not isinstance(self.xbmc_user,str):
      raise TypeError('param xbmc_user must be str')
    if self.xbmc_pass is not None and not isinstance(self.xbmc_pass,str):
      raise TypeError('param xbmc_pass must be str')
    if self.ping_freq is not None and not isinstance(self.ping_freq,int):
      raise TypeError('param ping_freq must be int')

    # lib dir lists must contain either str or valid samba dict
    try:
      self.validate_lib(self.audio_dirs)
    except Exception as e:
      e.message += ' in param audio_dirs'
      raise

    try:
      self.validate_lib(self.video_dirs)
    except Exception as e:
      e.message += ' in param video_dirs'
      raise

    # must be able to write to files
    self.can_write_file(self.log_file)
    self.can_write_file(self.bm_file)
    self.can_write_file(self.note_file)

    # account for later logic that checks if the pickle exists
    self.can_write_file(self.lib_file,True)

    # bw_list must be list of tuples of 3 strings
    for (i,l) in enumerate(self.bw_list):
      if not isinstance(l,tuple):
        raise TypeError('invalid type '+type(l).__name__+' for item '+str(i+1)+' in param bw_list')
      if len(l)!=3:
        raise ValueError('length of tuple '+str(i+1)+' in param bw_list must be 3 not '+str(len(l)))
      for x in l:
        if not isinstance(x,str):
          raise TypeError('invalid type '+type(l).__name__+' for tuple member of item '+str(i+1)+' in param bw_list')
      if l[0]!='b' and l[0]!='w':
        raise ValueError('first member of tuple '+str(i+1)+' in param bw_list must be "b" or "w"')

  def validate_lib(self,lib):
    """check lib list for valid types and entries"""

    for (i,l) in enumerate(lib):
      if isinstance(l,str):
        if not os.path.isdir(l):
          raise ValueError('path "'+l+'" is not a valid directory')
      elif isinstance(l,dict):
        if 'server' not in l.keys():
          raise KeyError('key "server" missing from item '+str(i+1))
        if 'share' not in l.keys():
          raise KeyError('key "share" missing from item '+str(i+1))
        for k in ['server','share','username','password']:
          if not isinstance(l[k],str):
            raise TypeError('value for key "'+k+'" must be of type str for item '+str(i+1))
      else:
        raise TypeError('invalid type '+type(l).__name__+' for item '+str(i+1))

  def can_write_file(self,fil,delete=False):
    """check if we have write permission to the specified file"""

    do_delete = (delete and not os.path.isfile(fil))
    
    f = open(fil,'a')
    f.close()
    
    if do_delete:
      os.remove(fil)

  def callback_message(self,conn,mess):
    """override to look at realjids and implement bwlist and redo"""

    usr = mess.getFrom()
    msg = mess.getBody()

    cmd = self.get_cmd(mess)

    # link_echo logic
    try:
      if cmd is None:
        if self.link_echo:
          if msg is not None:
            titles = []
            urls = re.findall(r'(https?://[^\s]+)', msg)
            if len(urls)==0:
              return
            for url in urls:
              r = requests.get(url)
              title = fromstring(r.content).findtext('.//title')
              titles.append(title)
            linkcount = 1
            reply = ""
            for title in titles:
              if title is not None:
                reply += "[" + str(linkcount)+ "] " + title + " "
                linkcount += 1
            self.send_simple_reply(mess, reply)
        return
    except Exception as e:
      self.log.error('Link echo - '+e.__class__.__name__+' - '+url)

    # account for double cmd_prefix = redo (e.g. !!)
    if self.cmd_prefix and cmd.startswith(self.cmd_prefix):
      new = self.remove_prefix(cmd)
      cmd = 'redo'
      if len(new.strip())>0:
        cmd += (' '+new.strip())

    args = cmd.split(' ')
    cmd_name = args[0]

    # convert MUC JIDs to real JIDs
    if (mess.getType()=='groupchat') and (usr in self.real_jids):
      usr = self.real_jids[usr]

    # check against bw_list
    usr = str(usr)
    for rule in self.bw_list:
      if (rule[1]!='*') and (rule[1] not in usr):
        continue
      if rule[2]!='*' and rule[2]!=cmd_name:
        continue
      applied = rule

    if applied[0]=='w':
      self.log.debug('Allowed "'+usr+'" to execute "'+cmd_name+'" with rule '+str(applied))
    else:
      self.log.debug('Denied "'+usr+'" from executing "'+cmd_name+'" with rule '+str(applied))
      self.send_simple_reply(mess,'You do not have permission to execute that command')
      return

    # redo command logic
    if cmd_name=='redo':
      self.log.debug('Redo cmd; original msg: "'+msg+'"')
      cmd = self.last_cmd
      if len(args)>1:
        cmd += (' '+' '.join(args[1:]))
        self.last_cmd = cmd
      if mess.getType()=='groupchat' and self.only_direct and not cmd.startswith(self.muc_nick):
        cmd = (self.muc_nick+' '+cmd)
      mess.setBody(cmd)
    else:
      self.last_cmd = cmd

    return super(SibylBot,self).callback_message(conn,mess)

################################################################################
# General Commands                                                             #
################################################################################

  @botcmd
  def redo(self,mess,args):
    """redo last command - redo [args]"""

    # this is a dummy function so it gets displayed in the help command
    # the real logic is at the end of callback_message()
    return

  @botcmd
  def last(self,mess,args):
    """display last command (from any chat)"""

    return self.last_cmd

  @botcmd
  def git(self,mess,args):
    """return a link to the github page"""

    return 'https://github.com/TheSchwa/sibyl'

  @botcmd
  def hello(self,mess,args):
    """reply if someone says hello"""

    return 'Hello world!'

  @botcmd
  def echo(self,mess,args):
    """echo some text"""

    return args

  @botcmd
  def realjid(self,mess,args):
    """return the real JID of the given MUC nick if known"""

    if '@' not in args:
      args = mess.getFrom().getStripped()+'/'+args
    if args.lower() in [str(jid).lower() for jid in self.seen]:
      return str(self.real_jids.get(args,args))
    return "I haven't seen that nick"

  @botcmd
  def say(self,mess,args):
    """if in a MUC, say this in it"""

    if not self.muc_room:
      return

    msg = self.build_message(args)
    msg.setTo(self.muc_room)
    msg.setType('groupchat')
    self.send_message(msg)

  @botcmd
  def all(self,mess,args):
    """append every user's nick to the front and say it in MUC"""

    if not self.muc_room:
      return

    s = ''
    frm = mess.getFrom()
    for jid in self.seen:
      if ((self.muc_room==jid.getStripped())
          and (self.muc_nick!=jid.getResource())
          and (jid!=frm)):
        s += (jid.getResource()+': ')

    if self.muc_room==frm.getStripped():
      frm = frm.getResource()
    else:
      frm = str(frm)

    self.say(None,s+'['+args+'] '+frm)

  @botcmd
  def network(self,mess,args):
    """reply with some network info"""

    s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    s.connect(('8.8.8.8',80))
    myip = s.getsockname()[0]
    s.close()

    piip = self.rpi_ip
    exip = requests.get('http://ipecho.net/plain').text.strip()

    return 'My IP - '+myip+' --- RPi IP - '+piip+' --- External IP - '+exip

  @botcmd
  def die(self,mess,args):
    """kill sibyl"""

    if not self.chat_ctrl:
      return 'chat_ctrl disabled'

    self.quit('Killed via chat_ctrl')

  @botcmd
  def reboot(self,mess,args):
    """restart sibyl"""

    if not self.chat_ctrl:
      return 'chat_ctrl disabled'

    DEVNULL = open(os.devnull,'wb')
    subprocess.Popen(['service','sibyl','restart'],
        stdout=DEVNULL,stderr=DEVNULL,close_fds=True)
    sys.exit()

  @botcmd
  def join(self,mess,args):
    """join a MUC - [roomJID nick pass]"""

    if not self.chat_ctrl:
      return 'chat_ctrl disabled'

    if args=='':
      self.rejoin(None,None)
    args = args.split(' ')
    if len(args)<2:
      args.append(self.nick_name)
    if len(args)<3:
      args.append(None)

    try:
      self.muc_join_room(args[0],args[1],args[2])
    except Exception as e:
      return 'Unable to join room'

    return 'Success joining room'

  @botcmd
  def rejoin(self,mess,args):
    """rejoin the configured MUC"""

    if not self.muc_room:
      return 'No room to rejoin; use the "join" command instead'
    args = self.muc_room+' '+self.muc_nick
    if self.muc_pass is not None:
      args += (' '+self.muc_pass)

    return self.join(None,args)

  @botcmd
  def tv(self,mess,args):
    """pass command to cec-client - tv (on|standby|as)"""

    # sanitize args
    args = ''.join([s for s in args if s.isalpha()])

    cmd = ['echo',args+' 0']
    cec = ['cec-client','-s']

    # execute echo command and pipe output to PIPE
    p = subprocess.Popen(cmd,stdout=subprocess.PIPE)

    # execute cec-client using PIPE as input and sending output to /dev/null
    DEVNULL = open(os.devnull,'wb')
    subprocess.call(cec,stdin=p.stdout,stdout=DEVNULL,close_fds=True)

  @botcmd
  def ups(self,mess,args):
    """get latest UPS tracking status - sibyl ups number"""

    # account for connectivity issues
    try:
      url = ('http://wwwapps.ups.com/WebTracking/track?track=yes&trackNums='
          + args + '&loc=en_us')
      page = requests.get(url).text

      # check for invalid tracking number
      if 'The number you entered is not a valid tracking number' in page:
        return 'Invalid tracking number: "'+args+'"'

      # find and return some relevant info
      start = page.find('Activity')
      (location,start) = getcell(start+1,page)
      (newdate,start) = getcell(start+1,page)
      (newtime,start) = getcell(start+1,page)
      (activity,start) = getcell(start+1,page)
      timestamp = newdate + ' ' + newtime
      return timestamp+' - '+location+' - '+activity

    except:
      return 'Unknown error accessing UPS website'

  @botcmd
  def wiki(self,mess,args):
    """return a link and brief from wikipedia - wiki title"""

    # search using wikipedia's opensearch json api
    url = ('http://en.wikipedia.org/w/api.php?action=opensearch&search='
        + args + '&format=json')
    response = requests.get(url)
    result = json.loads(response.text)
    title = result[1][0]
    text = result[2]

    # don't send the unicode specifier in the reply message
    try:
      text.remove(u'')
      text = '\n'.join(text)
    except ValueError:
      pass

    # send a link and brief back to the user
    url = result[3][0]
    return unicode(title)+' - '+unicode(url)+'\n'+unicode(text)

  @botcmd
  def logging(self,mess,args):
    """set the log level - log (critical|error|warning|info|debug|clear)"""

    if args=='clear':
      with open(self.log_file,'w') as f:
        return 'Log cleared'

    levels = ({'critical' : logging.CRITICAL,
               'error'    : logging.ERROR,
               'warning'  : logging.WARNING,
               'info'     : logging.INFO,
               'debug'    : logging.DEBUG})

    level = 'warning'
    if args in levels.keys():
      level = args

    self.log.setLevel(levels[level])
    return 'Logging level set to: '+level

  @botcmd
  def note(self,mess,args):
    """add a note - note (show|add|playing|remove) [body|num]"""

    args = args.split(' ')

    # default behavior is "show"
    if args[0]=='':
      args[0] = 'show'
    if args[0] not in ['show','add','playing','remove']:
      args.insert(0,'show')

    # set second parameter to make playing/add logic easier
    if len(args)<2:
      args.append('')
    else:
      args[1] = ' '.join(args[1:])

    # add the currently playing file to the body of the note then do "add"
    if args[0]=='playing':
      args[0] = 'add'

      pid = self.xbmc_active_player()
      result = self.xbmc('Player.GetProperties',{'playerid':pid,'properties':['time']})
      t = str(time2str(result['result']['time']))

      result = self.xbmc('Player.GetItem',{'playerid':pid,'properties':['file']})
      fil = os.path.basename(str(result['result']['item']['file']))

      args[1] += ' --- file "'+fil+'" at '+t

    # add the note to self.notes and self.note_file
    if args[0]=='add':
      if args[1]=='':
        return 'Note body cannot be blank'
      self.notes.append(args[1])
      self.note_write()
      return 'Added note #'+str(len(self.notes))+': '+args[1]

    # remove the specified note number from self.notes and rewrite self.note_file
    # notes are stored internally 0-indexed, but users see 1-indexed
    if args[0]=='remove':
      try:
        num = int(args[1])-1
      except ValueError as e:
        return 'Parameter to note remove must be an integer'
      if num<0 or num>len(self.notes)-1:
        return 'Parameter to note remove must be in [1,'+str(len(self.notes))+']'

      body = self.notes[num]
      del self.notes[num]
      self.note_write()
      return 'Removed note #'+str(num+1)+': '+body

    # otherwise show notes if they exist
    if len(self.notes)==0:
      return 'No notes'

    # if args[1] is blank then show all
    if args[1]=='':
      s = ''
      for (i,n) in enumerate(self.notes):
        s += '(Note #'+str(i+1)+'): '+n+', '
      return s[:-2]

    # if args[1] is a valid integer show that note
    try:
      num = int(args[1])-1
    except ValueError as e:
      num = None

    if num is not None:
      if num<0 or num>len(self.notes)-1:
        return 'Parameter to note show must be in [1,'+str(len(self.notes))+']'
      return 'Note #'+str(num+1)+': '+self.notes[num]

    # if args[1] is text, show matching notes
    search = args[1].lower()
    matches = [i for i in range(0,len(self.notes)) if search in self.notes[i].lower()]

    s = 'Found '+str(len(matches))+' matches: '
    for i in matches:
      s += '(Note #'+str(i+1)+'): '+self.notes[i]+', '
    return s[:-2]

################################################################################
# XBMC Commands                                                                #
################################################################################

  @botcmd
  def remote(self,mess,args):
    """execute remote buttons in order - remote (lrudeb)[...]"""

    cmds = {'u':'Input.Up',
            'd':'Input.Down',
            'l':'Input.Left',
            'r':'Input.Right',
            'e':'Input.Select',
            'b':'Input.Back'}

    raw = ''.join(args)
    cmd = [s for s in raw if s in cmds]
    for s in cmd:
      self.xbmc(cmds[s])

  @botcmd
  def volume(self,mess,args):
    """set the player volume percentage - volume %"""

    # if no arguments are passed, return the current volume
    if len(args.strip())==0:
      result = self.xbmc('Application.GetProperties',{'properties':['volume']})
      vol = result['result']['volume']
      return 'Current volume: '+str(vol)+'%'

    # otherwise try to set the volume
    args = args.replace('%','')
    try:
      val = int(args.strip())
    except ValueError as e:
      val = -1

    if val<0 or val>100:
      return 'Argument to "volume" must be an integer from 0-100'

    self.xbmc('Application.SetVolume',{'volume':val})

  @botcmd
  def subtitles(self,mess,args):
    """change the subtitles - subtitles (info|on|off|next|prev|set) [index]"""

    args = args.split(' ')
    pid = self.xbmc_active_player()
    if pid!=1:
      return 'No video playing'

    if args[0]=='prev':
      args[0] = 'previous'

    if args[0]=='on' or args[0]=='off' or args[0]=='next' or args[0]=='previous':
      self.xbmc('Player.SetSubtitle',{'playerid':pid,'subtitle':args[0]})
      if args[0]=='off':
        return
      subs = self.xbmc('Player.GetProperties',{'playerid':1,
          'properties':['currentsubtitle']})
      subs = subs['result']['currentsubtitle']
      return 'Subtitle: '+str(subs['index'])+'-'+subs['language']+'-'+subs['name']

    subs = self.xbmc('Player.GetProperties',{'playerid':1,
        'properties':['subtitles','currentsubtitle']})
    cur = subs['result']['currentsubtitle']
    subs = subs['result']['subtitles']

    if args[0]=='set':
      try:
        index = int(args[1])
      except ValueError as e:
        return 'Index must be an integer'

      if (index<0) or (index>len(subs)-1):
        return 'Index must be in [0,'+str(len(subs)-1)+']'
      cur = cur['index']
      if index==cur:
        return 'That is already the active subtitle'
      elif index<cur:
        diff = cur-index
        func = 'prev'
      else:
        diff = index-cur
        func = 'next'

      for i in range(0,diff):
        self.subtitles(None,func)
      return

    sub = []
    for i in range(0,len(subs)):
      for s in subs:
        if i==s['index']:
          sub.append(subs[i])
          continue

    s = 'Subtitles: '
    for x in sub:
      if x['index']==cur['index']:
        s += '('
      s += str(x['index'])+'-'+x['language']+'-'+x['name']
      if x['index']==cur['index']:
        s += ')'
      s += ', '
    return s[:-2]

  @botcmd
  def info(self,mess,args):
    """display info about currently playing file"""

    # abort if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return 'Nothing playing'

    # get file name
    result = self.xbmc('Player.GetItem',{'playerid':pid})
    name = result['result']['item']['label']

    # get speed, current time, and total time
    result = self.xbmc('Player.GetProperties',{'playerid':pid,'properties':['speed','time','totaltime']})
    current = result['result']['time']
    total = result['result']['totaltime']

    # translate speed: 0 = 'paused', 1 = 'playing'
    speed = result['result']['speed']
    status = 'playing'
    if speed==0:
      status = 'paused'

    playlists = ['Audio','Video','Picture']
    return playlists[pid]+' '+status+' at '+time2str(current)+'/'+time2str(total)+' - "'+name+'"'

  @botcmd
  def play(self,mess,args):
    """if xbmc is paused, resume playing"""

    self.playpause(0)

  @botcmd
  def pause(self,mess,args):
    """if xbmc is playing, pause"""

    self.playpause(1)

  @botcmd
  def stop(self,mess,args):
    """if xbmc is playing, stop"""

    # abort if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return None

    self.xbmc('Player.Stop',{"playerid":pid})

  @botcmd
  def prev(self,mess,args):
    """go to previous playlist item"""

    # abort if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return None

    # the first call goes to 0:00, the second actually goes back in playlist
    self.xbmc('Player.GoTo',{'playerid':pid,'to':'previous'})
    self.xbmc('Player.GoTo',{'playerid':pid,'to':'previous'})

  @botcmd
  def next(self,mess,args):
    """go to next playlist item"""

    # abort if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return None

    self.xbmc('Player.GoTo',{'playerid':pid,'to':'next'})

  @botcmd
  def jump(self,mess,args):
    """jump to an item# in the playlist - jump #"""

    # abort if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return None

    # try to parse the arg to an int
    try:
      num = int(args.split(' ')[-1])-1
      self.xbmc('Player.GoTo',{'playerid':pid,'to':num})
      return None
    except ValueError:
      return 'Playlist position must be an integer greater than 0'

  @botcmd
  def seek(self,mess,args):
    """go to a specific time - seek [hh:]mm:ss"""

    # abort if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return None

    # try to parse the arg as a time
    try:
      t = args.split(' ')[-1]
      c1 = t.find(':')
      c2 = t.rfind(':')
      s = int(t[c2+1:])
      h = 0
      if c1==c2:
        m = int(t[:c1])
      else:
        m = int(t[c1+1:c2])
        h = int(t[:c1])
      self.xbmc('Player.Seek',{'playerid':pid,'value':{'hours':h,'minutes':m,'seconds':s}})
    except ValueError:
      return 'Times must be in the format m:ss or h:mm:ss'

  @botcmd
  def restart(self,mess,args):
    """start playing again from 0:00"""

    # abort if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return None

    self.xbmc('Player.Seek',{'playerid':pid,'value':{'seconds':0}})

  @botcmd
  def hop(self,mess,args):
    """move forward or back - hop [small|big] [back|forward]"""

    # abort if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return None

    # check for 'small' (default) and 'big'
    s = ''
    if 'big' in args:
      s += 'big'
    else:
      s += 'small'

    # check for 'back' (default) and 'forward'
    if 'forward' in args:
      s += 'forward'
    else:
      s += 'backward'

    self.xbmc('Player.Seek',{'playerid':pid,'value':s})

  @botcmd
  def stream(self,mess,args):
    """stream from [YouTube, Twitch (Live)] - stream url"""

    msg = args

    # remove http:// https:// www. from start
    msg = msg.replace('http://','').replace('https://','').replace('www.','')

    # account for mobile links from youtu.be and custom start times
    if msg.lower().startswith('youtu.be'):
      tim = None
      if '?t=' in msg:
        tim = msg[msg.find('?t=')+3:]
        msg = msg[:msg.find('?t=')]
      msg = 'youtube.com/watch?v='+msg[msg.rfind('/')+1:]
      if tim:
        msg += ('&t='+tim)

    # account for "&start=" custom start times
    msg = msg.replace('&start=','&t=')

    # parse youtube links
    if msg.lower().startswith('youtube'):

      #check for custom start time
      tim = None
      if '&t=' in msg:
        start = msg.find('&t=')+3
        end = msg.find('&',start+1)
        if end==-1:
          end = len(msg)
        tim = msg[start:end]

        # get raw seconds from start time
        sec = 0
        if 'h' in tim:
          sec += 3600*int(tim[:tim.find('h')])
          tim = tim[tim.find('h')+1:]
        if 'm' in tim:
          sec += 60*int(tim[:tim.find('m')])
          tim = tim[tim.find('m')+1:]
        if 's' in tim:
          sec += int(tim[:tim.find('s')])
          tim = ''
        if len(tim)>0:
          sec = int(tim)

        # parse seconds to 'h:mm:ss'
        tim = {'hours':0,'minutes':0,'seconds':0}
        if int(sec/3600)>0:
          tim['hours'] = int(sec/3600)
          sec -= 3600*tim['hours']
        if int(sec/60)>0:
          tim['minutes'] = int(sec/60)
          sec -= 60*tim['minutes']
        tim['seconds'] = sec
        tim = time2str(tim)

      # remove feature, playlist, etc info from end and get vid
      if '&' in msg:
        msg = msg[:msg.find('&')]
      vid = msg[msg.find('watch?v=')+8:]

      # retrieve video info from webpage
      html = requests.get('http://youtube.com/watch?v='+vid).text
      title = html[html.find('<title>')+7:html.find(' - YouTube</title>')]
      title = title.replace('&#39;',"'").replace('&amp;','&')
      channel = html.find('class="yt-user-info"')
      start = html.find('>',channel+1)
      start = html.find('>',start+1)+1
      stop = html.find('<',start+1)
      channel = html[start:stop]

      # send xbmc request and seek if given custom start time
      self.xbmc('Player.Open',{'item':{'file':'plugin://plugin.video.youtube/play/?video_id='+vid}})
      if tim:
        self.seek(None,tim)

      # respond to the user with video info
      s = 'Streaming "'+title+'" by "'+channel+'" from YouTube'
      if tim:
        s += (' at '+tim)
      return s

    elif 'twitch' in msg.lower():

      vid = msg[msg.find('twitch.tv/')+10:]
      html = requests.get('http://twitch.tv/'+vid).text

      stream = html.find("property='og:title'")
      stop = html.rfind("'",0,stream)
      start = html.rfind("'",0,stop)+1
      stream = html[start:stop]

      title = html.find("property='og:description'")
      stop = html.rfind("'",0,title)
      start = html.rfind("'",0,stop)+1
      title = html[start:stop]

      response = self.xbmc('Player.Open',{'item':{'file':'plugin://plugin.video.twitch/playLive/'+vid}})
      return 'Streaming "'+title+'" by "'+stream+'" from Twitch Live'

    else:
      return 'Unsupported URL'

  @botcmd
  def search(self,mess,args):
    """search all paths for matches - search [include -exclude]"""

    name = args.split(' ')
    matches = []

    # search all library paths
    dirs = [self.lib_video_dir,self.lib_video_file,self.lib_audio_dir,self.lib_audio_file]
    for d in dirs:
      matches.extend(self.matches(d,name))

    if len(matches)==0:
      return 'Found 0 matches'

    # reply with matches based on max_matches setting
    if len(matches)>1:
      if self.max_matches<1 or len(matches)<=self.max_matches:
        return 'Found '+str(len(matches))+' matches: '+list2str(matches)
      else:
        return 'Found '+str(len(matches))+' matches'

    return 'Found '+str(len(matches))+' match: '+str(matches[0])

  @botcmd
  def videos(self,mess,args):
    """search and open a folder as a playlist - videos [include -exclude] [track#]"""

    return self.files(args,self.lib_video_dir,1)

  @botcmd
  def video(self,mess,args):
    """search and play a single video - video [include -exclude]"""

    return self.file(args,self.lib_video_file)

  @botcmd
  def audios(self,mess,args):
    """search and open a folder as a playlist - audios [include -exclude] [track#]"""

    return self.files(args,self.lib_audio_dir,0)

  @botcmd
  def audio(self,mess,args):
    """search and play a single audio file - audio [include -exclude]"""

    return self.file(args,self.lib_audio_file)

  @botcmd
  def fullscreen(self,mess,args):
    """toggle fullscreen"""

    self.xbmc('GUI.SetFullscreen',{'fullscreen':'toggle'})

  @botcmd
  def library(self,mess,args):
    """control media library - library (info|load|rebuild|save)"""

    # read the library from a pickle and load it into sibyl
    if args=='load':
      with open(self.lib_file,'r') as f:
        d = pickle.load(f)
      self.lib_last_rebuilt = d['lib_last_rebuilt']
      self.lib_last_elapsed = d['lib_last_elapsed']
      self.lib_video_dir = d['lib_video_dir']
      self.lib_video_file = d['lib_video_file']
      self.lib_audio_dir = d['lib_audio_dir']
      self.lib_audio_file = d['lib_audio_file']

      n = len(self.lib_audio_dir)+len(self.lib_video_dir)
      s = 'Library loaded from "'+self.lib_file+'" with '+str(n)+' files'
      self.log.info(s)
      return s

    # save sibyl's library to a pickle
    elif args=='save':
      d = ({'lib_last_rebuilt':self.lib_last_rebuilt,
            'lib_last_elapsed':self.lib_last_elapsed,
            'lib_video_dir':self.lib_video_dir,
            'lib_video_file':self.lib_video_file,
            'lib_audio_dir':self.lib_audio_dir,
            'lib_audio_file':self.lib_audio_file})
      with open(self.lib_file,'w') as f:
        pickle.dump(d,f,-1)

      s = 'Library saved to "'+self.lib_file+'"'
      self.log.info(s)
      return s

    # rebuild the library by traversing all paths then save it
    elif args=='rebuild':

      # when sibyl calls this method on init mess is None
      if mess is not None:
        t = sec2str(self.lib_last_elapsed)
        self.send_simple_reply(mess,'Working... (last rebuild took '+t+')')

      # time the rebuild and update library vars
      start = time.time()
      self.lib_last_rebuilt = time.asctime()
      self.lib_video_dir = self.find('dir',self.video_dirs)
      self.lib_video_file = self.find('file',self.video_dirs)
      self.lib_audio_dir = self.find('dir',self.audio_dirs)
      self.lib_audio_file = self.find('file',self.audio_dirs)
      self.lib_last_elapsed = int(time.time()-start)
      result = self.library(None,'save')

      s = 'Library rebuilt in '+sec2str(self.lib_last_elapsed)
      self.log.info(s)
      return s

    # default prints some info
    t = self.lib_last_elapsed
    s = str(int(t/60))+':'
    s += str(int(t-60*int(t/60))).zfill(2)
    n = len(self.lib_audio_dir)+len(self.lib_video_dir)
    return 'Rebuilt on '+self.lib_last_rebuilt+' in '+s+' with '+str(n)+' files'

  @botcmd
  def random(self,mess,args):
    """play random song - random [include -exclude]"""

    # check if a search term was passed
    name = args.split(' ')
    if args=='':
      matches = self.lib_audio_file
    else:
      matches = self.matches(self.lib_audio_file,name)

    if len(matches)==0:
      return 'Found 0 matches'

    # play a random audio file from the matches
    rand = random.randint(0,len(matches)-1)

    result = self.xbmc('Player.Open',{'item':{'file':matches[rand]}})
    if 'error' in result.keys():
      s = 'Unable to open: '+matches[rand]
      self.log.error(s)
      return s

    self.xbmc('GUI.SetFullscreen',{'fullscreen':True})

    return 'Playing "'+matches[rand]+'"'

  @botcmd
  def bookmark(self,mess,args):
    """manage bookmarks - bookmarks [show|set|remove] [name]"""

    args = args.split(' ')
    if args[0]=='set':
      # check if last_played is set
      if self.last_played is None:
        return 'No active audios or videos playlist to bookmark'

      # check if a name was passed
      name = self.last_played[1]
      args = args[1:]
      if len(args)>0:
        name = args[0]

      # get info for bookmark
      pid = self.last_played[0]
      path = self.last_played[1]
      result = self.xbmc('Player.GetProperties',{'playerid':pid,'properties':['position','time']})
      pos = result['result']['position']
      t = str(time2str(result['result']['time']))
      add = time.time()
      result = self.xbmc('Player.GetItem',{'playerid':pid,'properties':['file']})
      fil = os.path.basename(str(result['result']['item']['file']))

      # note that the position is stored 0-indexed
      self.bm_store[name] = {'path':path,'add':add,'time':t,'pid':pid,'pos':pos,'file':fil}
      self.bm_update(name,self.bm_store[name])
      return 'Bookmark added for "'+name+'" item '+str(pos+1)+' at '+t

    elif args[0]=='remove':
      if len(args)==1:
        return 'To remove all bookmarks use "bookmarks remove *"'
      if not self.bm_remove(args[1]):
        return 'Bookmark "'+name+'" not found'
      return

    elif args[0]=='show':
      args = args[1:]

    # actual code for show function because this is default behavior
    if len(self.bm_store)==0:
      return 'No bookmarks'

    # if no args are passed return all bookmark names
    matches = self.bm_store.keys()
    if len(args)==0 or args[0]=='':
      return 'There are '+str(len(matches))+' bookmarks: '+str(matches)

    # if a search term was passed find matches and display them
    search = ' '.join(args).lower()
    matches = [m for m in matches if search in m.lower()]

    entries = []
    for m in matches:
      item = self.bm_store[m]
      pos = item['pos']
      t = item['time']
      fil = item['file']
      entries.append('"'+m+'" at item '+str(pos+1)+' and time '+t+' which is "'+fil+'"')
    if len(entries)==1:
      return 'Found 1 bookmark: '+str(entries[0])
    return 'Found '+str(len(entries))+' bookmarks: '+list2str(entries)

  @botcmd
  def resume(self,mess,args):
    """resume playing a playlist - resume [name] [next]"""

    # if there are no bookmarks return
    if len(self.bm_store)==0:
      return 'No bookmarks'

    # check for "next" as last arg
    opts = args.strip().split(' ')
    start_next = (opts[-1]=='next')
    if start_next:
      opts = opts[:-1]
      args = ' '.join(opts)

    # check if a name was passed
    name = self.bm_recent()
    if len(args)>0:
      name = args

    # get info from bookmark
    if name not in self.bm_store.keys():
      return 'No bookmark named "'+name+'"'
    item = self.bm_store[name]
    path = item['path']
    pid = item['pid']
    pos = item['pos']
    t = item['time']

    # open the directory as a playlist
    if start_next:
      pos += 1

    # note that the user-facing functions assume 1-indexing
    args = '"'+path+'" '+str(pos+1)
    if pid==0:
      result = self.audios(None,args)
    elif pid==1:
      result = self.videos(None,args)
    else:
      return 'Error in bookmark for "'+name+'": invalid pid'+str(pid)

    if not start_next:
      self.seek(None,t)
      result += ' at '+t

    return result

################################################################################
# Helper Functions                                                             #
################################################################################

  def xbmc(self,method,params=None):
    """wrapper method to always provide IP to static method"""

    return xbmc(self.rpi_ip,method,params,self.xbmc_user,self.xbmc_pass)

  def xbmc_active_player(self):
    """wrapper method to always provide IP to static method"""

    return xbmc_active_player(self.rpi_ip,self.xbmc_user,self.xbmc_pass)

  def playpause(self,target):
    """helper function for play() and pause()"""

    # return None if nothing is playing
    pid = self.xbmc_active_player()
    if pid is None:
      return None

    # check player status before sending PlayPause command
    speed = self.xbmc('Player.GetProperties',{'playerid':pid,'properties':["speed"]})
    speed = speed['result']['speed']
    if speed==target:
      self.xbmc('Player.PlayPause',{"playerid":pid})

  def files(self,args,dirs,pid):
    """helper function for videos() and audios()"""

    # check for item# as last arg
    args = args.split(' ')
    num = None
    try:
      num = int(args[-1])-1
    except ValueError:
      pass

    # default is 0 if not specified
    if num is None:
      num = 0
      name = args
    else:
      name = args[:-1]

    # find matches and respond if len(matches)!=1
    matches = self.matches(dirs,name)

    if len(matches)==0:
      return 'Found 0 matches'

    # default to top dir if every match is a sub dir
    matches = reducetree(matches)

    if len(matches)>1:
      if self.max_matches<1 or len(matches)<=self.max_matches:
        return 'Found '+str(len(matches))+' matches: '+list2str(matches)
      else:
        return 'Found '+str(len(matches))+' matches'

    # if there was 1 match, add the whole directory to a playlist
    # also check for an error opening the directory
    self.xbmc('Playlist.Clear',{'playlistid':pid})

    result = self.xbmc('Playlist.Add',{'playlistid':pid,'item':{'directory':matches[0]}})
    if 'error' in result.keys():
      s = 'Unable to open: '+matches[0]
      self.log.error(s)
      return s

    self.xbmc('Player.Open',{'item':{'playlistid':pid,'position':num}})
    self.xbmc('GUI.SetFullscreen',{'fullscreen':True})

    # set last_played for bookmarking
    self.last_played = (pid,matches[0])

    return 'Playlist from "'+matches[0]+'" starting with #'+str(num+1)

  def file(self,args,dirs):
    """helper function for video() and audio()"""

    name = args.split(' ')

    # find matches and respond if len(matches)!=1
    matches = self.matches(dirs,name)

    if len(matches)==0:
      return 'Found 0 matches'

    if len(matches)>1:
      if self.max_matches<1 or len(matches)<=self.max_matches:
        return 'Found '+str(len(matches))+' matches: '+list2str(matches)
      else:
        return 'Found '+str(len(matches))+' matches'

    # if there was 1 match, play the file, and check for not found error
    result = self.xbmc('Player.Open',{'item':{'file':matches[0]}})
    if 'error' in result.keys():
      s = 'Unable to open: '+matches[0]
      self.log.error(s)
      return s

    self.xbmc('GUI.SetFullscreen',{'fullscreen':True})

    # clear last_played
    self.last_played = None

    return 'Playing "'+matches[0]+'"'

  def matches(self,lib,args):
    """helper function for search(), files(), and file()"""

    # implement quote blocking
    name = []
    quote = False
    s = ''
    for arg in args:
      if arg.startswith('"') and arg.endswith('"'):
        name.append(arg[1:-1])
      elif arg.startswith('"'):
        quote = True
        s += arg[1:]
      elif arg.endswith('"'):
        quote = False
        s += (' '+arg[:-1])
        name.append(s)
        s = ''
      elif quote:
        s += (' '+arg)
      else:
        name.append(arg)
    if quote:
      name.append(s.replace('"',''))

    # find matches
    matches = []
    for entry in lib:
      try:
        if checkall(name,entry):
          matches.append(entry)
      except:
        pass
    matches.sort()
    return matches

  def find(self,fd,dirs):
    """helper function for library()"""

    paths = []
    smbpaths = []

    # sort paths into local and samba based on whether they're tuples
    for path in dirs:
      if isinstance(path,dict):
        smbpaths.append(path)
      else:
        paths.append(path)

    result = []

    # find all matching directories or files depending on fd parameter
    for path in paths:
      try:
        if fd=='dir':
          contents = rlistdir(path)
        else:
          contents = rlistfiles(path)
        for entry in contents:
          try:
            result.append(str(entry))
          except UnicodeError:
            self.log.error('Unicode error parsing path "'+entry+'"')
      except OSError:
        self.log.error('Unable to traverse "'+path+'"')

    # same as above but for samba shares
    for path in smbpaths:
      try:
        smb = SambaClient(**path)
        if fd=='dir':
          contents = rsambadir(smb,'/')
        else:
          contents = rsambafiles(smb,'/')
        smb.close()
        for entry in contents:
          try:
            result.append(str(entry))
          except UnicodeError:
            self.log.error('Unicode error parsing path "'+entry+'"')
      except SambaClientError:
        self.log.error('Unable to traverse "smb://'+path['server']+'/'+path['share']+'"')

    return result

  def bm_parse(self):
    """read the bm_file into a dict"""

    d = {}
    with open(self.bm_file,'r') as f:
      lines = [l.strip() for l in f.readlines() if l!='\n']

    # tab-separated each line is: name path pid position file time added
    for l in lines:
      (name,props) = self.bm_unformat(l)
      d[name] = props

    self.log.info('Parsed '+str(len(d))+' bookmarks from "'+self.bm_file+'"')
    self.bm_store = d
    return d

  def bm_update(self,name,props):
    """add or modify the entry for name with props in dict and file
    returns True if name was modified or False if name was added"""

    result = self.bm_remove(name)
    self.bm_add(name,props)
    return result

  def bm_add(self,name,props):
    """add the entry for name with props to dict and file. Note
    that this function could add duplicates without proper checking"""

    self.bm_store[name] = props

    # the bookmark file should always end in a newline
    with open(self.bm_file,'a') as f:
      f.write(self.bm_format(name,props)+'\n')

  def bm_remove(self,name):
    """remove the entry for name from dict and file if it exists
    returns False if name was not found or True if name was removed"""

    # passing "*" removes all bookmarks
    if name=='*':
      self.bm_store = {}
      with open(self.bm_file,'w') as f:
        f.write('')
      return True

    # return False if name does not exist
    if name not in self.bm_store.keys():
      return False

    del self.bm_store[name]

    with open(self.bm_file,'r') as f:
      lines = f.readlines()

    lines = [l for l in lines if l.split('\t')[0]!=name]

    with open(self.bm_file,'w') as f:
      f.writelines(lines)

    # return True if name was removed
    return True

  def bm_format(self,name,props):
    """return props as a string formatted for the bm_file"""

    order = ['path','pid','pos','file','time','add']
    for prop in order:
      name += ('\t'+str(props[prop]))
    return name

  def bm_unformat(self,line):
    """return the name and props from the line as a tuple"""

    line = line.strip()
    (name,path,pid,pos,fil,t,add) = line.split('\t')
    pid = int(pid)
    pos = int(pos)
    add = float(add)
    props = {'path':path,'add':add,'time':t,'pid':pid,'pos':pos,'file':fil}

    # name is str, props is dict
    return (name,props)

  def bm_recent(self):
    """return the most recent bookmark from the dict"""

    name = None
    add = 0
    for k in self.bm_store.keys():
      t = self.bm_store[k]['add']
      if t > add:
        name = k
        add = t

    return name

  def note_parse(self):
    """read the note file into a list"""

    with open(self.note_file,'r') as f:
      return [l.strip() for l in f.readlines() if l!='\n']

  def note_write(self):
    """write self.notes to self.note_file"""

    lines = [l+'\n' for l in self.notes]
    lines.append('\n')

    with open(self.note_file,'w') as f:
      f.writelines(lines)

################################################################################
# Static Functions                                                             #
################################################################################

def xbmc(ip,method,params=None,user=None,pword=None):
  """make a JSON-RPC request to xbmc and return the resulti as a dict
  or None if ConnectionError or Timeout"""

  # build a json call with the requests library
  p = {'jsonrpc':'2.0','id':1,'method':method}
  if params is not None:
    p['params'] = params

  url = 'http://'+ip+'/jsonrpc'
  headers = {'content-type':'application/json'}
  payload = p
  params = {'request':json.dumps(payload)}

  r = requests.get(url,params=params,headers=headers,auth=(user,pword),timeout=60)
  # return the response from xbmc as a dict
  return json.loads(r.text)

def xbmc_active_player(ip,user=None,pword=None):
  """return the id of the currently active player or None"""

  j = xbmc(ip,'Player.GetActivePlayers',user=user,pword=pword)
  if len(j['result'])==0:
    return None
  return j['result'][0]['playerid']

def time2str(t):
  """change the time dict to a string"""

  # based on the format returned by xbmc's json api
  s = ''
  hr = str(t['hours'])
  mi = str(t['minutes'])
  sec = str(t['seconds'])

  # only include hours if they exist
  if t['hours']>0:
    s += (hr+':')
    s+= (mi.zfill(2)+':')
  else:
    s+= (mi+':')
  s+= sec.zfill(2)

  return s

def sec2str(t):
  """change the time in seconds to a string"""

  s = int(t)

  h = int(s/3600)
  s -= 3600*h
  m = int(s/60)
  s -= 60*m

  return time2str({'hours':h,'minutes':m,'seconds':s})

def rlistdir(path):
  """list folders recursively"""

  alldirs = []
  for (cur_path,dirnames,filenames) in os.walk(path):
    for dirname in dirnames:
      alldirs.append(os.path.join(cur_path,dirname)+'/')
  return alldirs

def rlistfiles(path):
  """list files recursively"""

  allfiles = []
  for (cur_path,dirnames,filenames) in os.walk(path):
    for filename in filenames:
      allfiles.append(os.path.join(cur_path,filename))
  return allfiles

def rsambadir(smb,path):
  """recursively list directories"""

  alldirs = []
  items = smb.listdir(path)
  for item in items:
    cur_path = os.path.join(path,item)
    if smb.isdir(cur_path):
      alldirs.append(str('smb:'+smb.path+cur_path)+'/')
      alldirs.extend(rsambadir(smb,cur_path))
  return alldirs

def rsambafiles(smb,path):
  """recursively list files"""

  allfiles = []
  items = smb.listdir(path)
  for item in items:
    cur_path = os.path.join(path,item)
    if smb.isfile(cur_path):
      allfiles.append(str('smb:'+smb.path+cur_path))
    elif smb.isdir(cur_path):
      allfiles.extend(rsambafiles(smb,cur_path))
  return allfiles

def checkall(l,s):
  """make sure all strings in l are in s unless they start with '-' """

  for x in l:
    if (x[0]!='-') and (x.lower() not in s.lower()):
      return False
    if (x[0]=='-') and (x[1:].lower() in s.lower()) and (len(x[1:])>0):
      return False
  return True

def getcell(start,page):
  """return the contents of the next table cell and its end index"""

  # used in the ups command to make life easier
  start = page.find('<td',start+1)
  start = page.find('>',start+1)
  stop = page.find('</td>',start+1)
  s = page[start+1:stop].strip()
  s = s.replace('\n',' ').replace('\t',' ')
  return (' '.join(s.split()),stop)

def list2str(l):
  """return the list on separate lines"""

  # makes match lists look much better in chat or pastebin
  s = '['
  for x in l:
    s += ('"'+x+'",\n')
  return (s[:-2]+']')

def reducetree(paths):
  """if all paths are sub-dirs of a common root, return the root"""

  shortest = sorted(paths,key=len)[0]
  for path in paths:
    if not path.startswith(shortest):
      return paths
  return [shortest]
