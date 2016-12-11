#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Sibyl: A modular Python chat bot framework
# Copyright (c) 2016 Jonathan Frederickson <jonathan@terracrypt.net>
#
# This file is part of Sibyl.
#
# Sibyl is free software; you can redistribute it and/or modify
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
################################################################################

from sibyl.lib.protocol import User,Room,Message,Protocol
from sibyl.lib.protocol import (PingTimeout,ConnectFailure,AuthFailure,
    ServerShutdown)

from sibyl.lib.decorators import botconf

from matrix_client.client import MatrixClient
from matrix_client.api import MatrixRequestError, MatrixHttpApi
import matrix_client.user as mxUser
import matrix_client.room as mxRoom

################################################################################
# Config options
################################################################################

@botconf
def conf(bot):
  return [
    {"name": "username", "req": True},
    {"name": "password", "req": True},
    {"name": "server", "req": True}
  ]

################################################################################
# User sub-class
################################################################################

class MatrixUser(User):

  # called on object init; the following are already created by __init__:
  #   self.protocol = (Protocol) name of this User's protocol as a str
  #   self.typ = (int) either Message.PRIVATE or Message.GROUP
  #   self.real = (User) the "real" User behind this user (defaults to self)
  # @param user (object) a full username
  def parse(self,user):
    if(isinstance(user,mxUser.User)):
      self.user = user
    elif(isinstance(user,basestring)):
      self.user = self.protocol.client.get_user(user)
    else:
      raise TypeError("User parameter to parse must be a string")

  # @return (str) the username in private chat or the nick name in a room
  def get_name(self):
    return self.user.get_display_name()

  # @return (str) the username without resource identifier
  def get_base(self):
    return self.user.user_id

  # @param other (object) you must check for class equivalence
  # @return (bool) True if self==other (including resource)
  def __eq__(self,other):
    if(not isinstance(other,MatrixUser)):
      return False
    return(self.get_base() == other.get_base())

  # @return (str) the full username
  def __str__(self):
    return self.get_base()

################################################################################
# Room sub-class
################################################################################

class MatrixRoom(Room):

  # called on object init; the following are already created by __init__:
  #   self.protocol = name of this Room's protocol as a str
  #   self.nick = the nick name to use in the room (defaults to None)
  #   self.pword = the password for this room (defaults to None)
  # @param name (object) a full roomid
  def parse(self,name):
    if(isinstance(name,mxRoom.Room)):
      self.room = room
    elif(isinstance(user,basestring)):
      self.room = mxRoom(self.protocol.client,name) # [TODO] Assumes a room ID for now
    else:
      raise TypeError("User parameter to parse must be a string")

  # the return value must be the same for equal Rooms and unique for different
  # @return (str) the name of this Room
  def get_name(self):
    return self.room.room_id

  # @param other (object) you must check for class equivalence
  # @return (bool) true if other is the same room (ignore nick/pword if present)
  def __eq__(self,other):
    if(isinstance(other,MatrixRoom)):
      return self.get_name() == other.get_name()
    else:
      return False

################################################################################
# Protocol sub-class
################################################################################

class MatrixProtocol(Protocol):

  # called on bot init; the following are already created by __init__:
  #   self.bot = SibylBot instance
  #   self.log = the logger you should use
  def setup(self):
    self.connected = False
    self.rooms = {}
    self.bot.add_var("credentials",persist=True)

    # Create a client in setup() because we might use self.client before
    # connect() is called
    print(self.opt('matrix.server'))
    homeserver = self.opt('matrix.server')
    self.client = MatrixClient(homeserver)

  # @raise (ConnectFailure) if can't connect to server
  # @raise (AuthFailure) if failed to authenticate to server
  def connect(self):
    homeserver = self.opt('matrix.server')
    user = self.opt('matrix.username')
    pw = self.opt('matrix.password')

    self.log.debug("Connecting to %s" % homeserver)

    try:
      self.log.debug("Logging in as %s" % user)

      # Log in with the existing access token if we already have a token
      if(bot.credentials and bot.credentials[0] == user):
        self.client = MatrixClient(homeserver, token=bot.credentials[1])
      # Otherwise, log in with the configured username and password
      else:
        token = self.client.login_with_password(user,pw)
        bot.credentials = (user, token)

      self.rooms = self.client.get_rooms()
      self.log.debug("Already in rooms: %s" % self.rooms)

      # Connect to Sibyl's message callback
      self.client.add_listener(self._cb_message)
       
    except MatrixRequestError as e:
      if(e.code == 403):
        self.log.debug("Credentials incorrect! Maybe your access token is outdated?")
        raise AuthFailure
      else:
        self.log.debug("Failed to connect to homeserver!")
        raise ConnectFailure

  # @return (bool) True if we are connected to the server
  def is_connected(self):
    raise NotImplementedError

  # called whenever the bot detects a disconnect as insurance
  def disconnected(self):
    raise NotImplementedError

  # receive/process messages and call bot._cb_message()
  # must ignore msgs from myself and from users not in any of our rooms
  # @param wait (int) time to wait for new messages before returning
  # @call bot._cb_message(Message) upon receiving a valid status or message
  # @raise (PingTimeout) if implemented
  # @raise (ConnectFailure) if disconnected
  # @raise (ServerShutdown) if server shutdown
  def process(self,wait=0):
    self.client.start_listener_thread()

  #def messageHandler(self):
  #  raise NotImplementedError

  # called when the bot is exiting for whatever reason
  # NOTE: sibylbot will already call part_room() on every room in get_rooms()
  def shutdown(self):
    raise NotImplementedError

  # send a message to a user
  # @param text (str,unicode) text to send
  # @param to (User,Room) recipient
  def send(self,text,to):
    to.room.send_text(text)

  # send a message with text to every user in a room
  # optionally note that the broadcast was requested by a specific User
  # @param text (str,unicode) body of the message
  # @param room (Room) room to broadcast in
  # @param frm (User) [None] the User requesting the broadcast
  def broadcast(self,text,room,frm=None):
    raise NotImplementedError

  # join the specified room using the specified nick and password
  # @param room (Room) the room to join
  # @call bot._cb_join_room_success(room) on successful join
  # @call bot._cb_join_room_failure(room,error) on failed join
  def join_room(self,room):
    try:
      res = self.client.join_room(room.room_id)
      bot._cb_join_room_success(MatrixRoom(res))
    except MatrixRequestError as e:
      bot._cb_join_room_failure(room, e.message)

  # part the specified room
  # @param room (Room) the room to leave
  def part_room(self,room):
    raise NotImplementedError

  # helper function for get_rooms() for protocol-specific flags
  # only needs to handle: FLAG_PARTED, FLAG_PENDING, FLAG_IN, FLAG_ALL
  # @param flag (int) one of Room.FLAG_* enums
  # @return (list of Room) rooms matching the flag
  def _get_rooms(self,flag):
    raise NotImplementedError

  # @param room (Room) the room to query
  # @return (list of User) the Users in the specified room
  def get_occupants(self,room):
    raise NotImplementedError

  # @param room (Room) the room to query
  # @return (str) the nick name we are using in the specified room
  def get_nick(self,room):
    raise NotImplementedError

  # @param room (Room) the room to query
  # @param nick (str) the nick to examine
  # @return (User) the "real" User behind the specified nick/room
  def get_real(self,room,nick):
    raise NotImplementedError

  # @return (User) our username
  def get_user(self):
    return MatrixUser(self,self.opt('matrix.username'),Message.GROUP)

  # @param user (str) a user id to parse
  # @param typ (int) either Message.GROUP or Message.PRIVATE
  # @param real (User) [self] the "real" user behind this user
  # @return (User) a new instance of this protocol's User subclass
  def new_user(self,user,typ,real=None):
    return MatrixUser(self,user,typ,real)

  # @param name (object) the identifier for this Room
  # @param nick (str) [None] the nick name to use in this Room
  # @param pword (str) [None] the password for joining this Room
  # @return (Room) a new instance of this protocol's Room subclass
  def new_room(self,room_id_or_alias,nick=None,pword=None):
    return MatrixRoom(self,room_id_or_alias,nick,pword)

################################################################################
# Helper functions
################################################################################
