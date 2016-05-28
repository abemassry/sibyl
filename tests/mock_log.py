#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Sibyl: A modular Python chat bot framework
# Copyright (c) 2015-2016 Joshua Haas <jahschwa.com>
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

import time,logging

class MockLog(object):

  def __init__(self):
    self.msgs = []
  def log(self,lvl,msg):
    self.msgs.append('%s | %s | %s' % (time.asctime(),lvl,msg))

  def debug(self,msg):
    self.log('DEB',msg)
  def info(self,msg):
    self.log('INF',msg)
  def warning(self,msg):
    self.log('WAR',msg)
  def error(self,msg):
    self.log('ERR',msg)
  def critical(self,msg):
    self.log('CRI',msg)

  def getEffectiveLevel(self):
    return logging.DEBUG
