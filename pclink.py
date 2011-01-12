#!/usr/bin/python
# -*- coding: latin-1 -*-
"""pclink.py: replacement server for the Philips Media Manager
for the Streamium MC-i200 and MC-i250
"""

import urllib

##################################################
#
# Important options
#
##################################################

PCLINK_SERVER_NAME = "Python PCLink"
SAVEFILE = "pclink.dat"
SCAN_ROOT = "D:\\Tunes\\Tagged"
URL_PREFIX = "http://192.168.100.6:80/music/Tagged"
CALC_LENGTH = True
RESCAN_ALL = False
PREFER_ALBUM_TAG_TO_FOLDER_NAME = True
TESTING_WITH_STREAMYTEST = False

streams = { 'WWOZ':'http://wwoz-sc.streamguys.com/wwoz-hi.mp3', 'DI Ambient':'http://205.188.215.228:8006' }

##################################################
#
# End of important options
#
##################################################

__version__ = "0.90"
__author__ = "Lindsey Smith (lindsey.smith@gmail.com)"
__copyright__ = "(C) 2010 by Lindsey Smith, Released under GNU GPL 2 License"
___contributors__ = [ "Lindsey Smith (lindsey.smith@gmail.com)" ]

from socket import socket, AF_INET, SOCK_DGRAM
from socket import *
from struct import *
import xml.dom.minidom
import xml.sax.saxutils
import logging
import threading
import string
import time
import eyeD3
import os
from os.path import join
import BaseHTTPServer
import SimpleHTTPServer
import cPickle as pickle
import operator

HELLO_UDP_PORT = 42591
LISTEN_TCP_PORT = 42951 
RECV_BUF_SIZE = 4096

QUOTE_SAFE = '/'

nodes = []

##################################################
#
# Global utility things
#
##################################################

logger = logging.getLogger("streamium")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter("%(levelname)s - %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)


def filenametoutf8(orgmsg):
      if not orgmsg:
         return orgmsg
      
      msg = u''
      for c in orgmsg:
         try:
            if ord(c) < 0x80: msg += c
            elif ord(c) < 0xC0:
               msg += ('%%C2%%%X' % (ord(c)-64))
            else:
               msg += ('%%C3%%%X' % (ord(c)-64))
         except Exception, e:
            logger.info( u'Exception encoding char ''%c''' % (c))
            
      return msg

def dottedQuadToNum(ip):
   "convert decimal dotted quad string to long integer"
   return unpack('L',inet_aton(ip))[0]

def numToDottedQuad(n):
   "convert long int to dotted quad string"
   return inet_ntoa(pack('L',n))

def xmlwrap(key, value=""):

   # hack for streamytest.pl compatibility
   if TESTING_WITH_STREAMYTEST and value and hasattr(value, 'lower'): 
      value = value.encode('ascii', 'replace')
      value = xml.sax.saxutils.escape(value)

   ret = ''
   if value:
      ret = '<%s>%s</%s>' % (key, value, key)
   else:
      ret = '<%s/>' % key
   return ret


##################################################
#
# Thready stuff
#
##################################################

class AnnouncementListenerThread ( threading.Thread ):
   def __init__ ( self, myIPStr, inPortNum):
      self.inPortNum = inPortNum
      self.myIPStr = myIPStr
      self.myListenSocket = None
      self.myResponseSocket = None
      threading.Thread.__init__ ( self )
   
   def run ( self ):
      logger.info('Starting announcement listener')
      logger.debug('Creating UDP listener socket @ %s:%d' % (self.myIPStr, self.inPortNum))
      self.myListenSocket = socket(AF_INET, SOCK_DGRAM)
      logger.debug('Binding UDP listener socket')
      self.myListenSocket.bind((self.myIPStr, self.inPortNum))
      
      while True:
        logger.info('Listening...')
        (data, addr) = self.myListenSocket.recvfrom( RECV_BUF_SIZE )
        logger.debug("Received UDP announcement packet from: " + `addr`)
        logger.debug("UDP annoucement packet contents: " + `data`)
        self.handleclientbcast(data, addr)
         
   def handleclientbcast(self, data, addr):
      # TODO: check bcast validity
      msg = '<PCLinkServer><Version>1.0</Version><VendorID>MUSICMATCH</VendorID><name>%s</name><ShortName>%s</ShortName><IP>%s</IP><Port>51111</Port></PCLinkServer>' % (PCLINK_SERVER_NAME, PCLINK_SERVER_NAME, dottedQuadToNum(self.myIPStr))
      logger.debug('my ip %s to int %d' % (self.myIPStr, dottedQuadToNum(self.myIPStr)))
      doc = xml.dom.minidom.parseString(data)
      names = doc.getElementsByTagName('Port')
      port = htons(string.atoi(names[0].childNodes[0].nodeValue))
      if self.myResponseSocket:
         logger.info( 'Closing existing listener socket' )
         self.myResponseSocket.close
         self.myResponseSocket = None
      try:
         self.myResponseSocket = socket(AF_INET, SOCK_STREAM)
      except Exception, e:
         logger.info( 'self.myResponseSocket = socket(AF_INET, SOCK_STREAM) --> %s' % e )
      try:         
         self.myResponseSocket.connect((addr[0], port))
         self.myResponseSocket.send(msg)
         self.myResponseSocket.close()
      except Exception, e:
         logger.info ('exception reusing myResponseSocket: s' %e)


class CommandListenerThread ( threading.Thread ):
   def __init__ ( self, myIPStr, inPortNum):
      self.inPortNum = inPortNum
      self.myIPStr = myIPStr
      self.myListenSocket = None
      threading.Thread.__init__ ( self )
   
   def run ( self ):
      logger.info('Starting command listener')
      logger.debug('Creating command TCP listener socket @ %s:%d' % (self.myIPStr, self.inPortNum))
      self.myListenSocket = socket(AF_INET, SOCK_STREAM)
      logger.debug('Binding TCP listener socket')
      self.myListenSocket.bind((self.myIPStr, self.inPortNum))
      
      while True:      
         logger.info('Listening for commands...')
         self.myListenSocket.listen(1)
         conn, addr = self.myListenSocket.accept()
         logger.info('Connected from: %s', addr)

         data = ''
         packet = ''
         while True:
            logger.debug("Recv'ing command data")
            packet = conn.recv(RECV_BUF_SIZE)
            if not packet: break
            data += packet
            if len(packet) < RECV_BUF_SIZE: break
            
         self.handleclientcommand(conn, data)
         logger.debug('Closing command connection')
         conn.close()

   def findsuperscroll(self, node, scrollto):
      logger.info('findsuperscroll("%s", "%s")' % (node.name, scrollto))
      
      for i in range(len(node.links)):
         item=nodes[node.links[i]]
         if scrollto[0] <= item.name[0]:
            logger.info('findsuperscroll("%s") found %s' % (scrollto, item.name))
            return i
            
      return 0
		 
   def handleclientcommand(self, conn, data):
      try:
         xmldata = data[data.find('<'):]
      except:
         logger.info('Received invalid data?')
         return
      
      logger.debug("XML command data: %s" % xmldata)
      try:
         doc = xml.dom.minidom.parseString(xmldata)
      except:
         logger.info('Received invalid XML')
         return

      nodeid = 0
      numelem = 0
      fromindex = 0
      superscroll = ''
      try:
         nodeidelem = doc.getElementsByTagName('nodeid')
         numelemelem = doc.getElementsByTagName('numelem')
         if nodeidelem: nodeid = int(nodeidelem[0].childNodes[0].nodeValue)
         numelem = int(numelemelem[0].childNodes[0].nodeValue)
         fromindexelem = doc.getElementsByTagName('fromindex')
         if fromindexelem: fromindex = int(fromindexelem[0].childNodes[0].nodeValue)
         superscrollelem = doc.getElementsByTagName('superscroll')
         if superscrollelem: superscroll = superscrollelem[0].childNodes[0].nodeValue[0]
      except:
         logger.info('Received inedible XML')
         return
      logger.debug('nodeid: %d; numelem: %d; fromindex: %d; superscroll: %s' % (nodeid, numelem, fromindex, superscroll))
      
      if superscroll:
         fromindex = self.findsuperscroll(nodes[nodeid], superscroll)
	  
      orgmsg = nodes[nodeid].buildresponse(numelem, fromindex)
      msg=''
      for c in orgmsg:
         msg += chr(ord(c))
      header = "HTTP/1.0 200 OK\r\nAccept-Ranges: bytes\r\nContent-Length: %d\r\nContent-Type: text/xml\r\n\r\n" % len(msg)
      response = header+msg
      conn.send(response)

class PCLinkNode:
   def __init__(self):
      self.name = ''
      self.links = []
      self.superscroll = None
      self.hasfiles = False

      self.album = None
      self.artist = None
      self.genre = None
      self.playlength = None
      self.isvalidmp3 = False

      self.filepath = ''
      self.nodeid = -1

      self.relativeurl = ''

   def processmp3(self, filepath, basepath):
        self.filepath = filepath

        try:
            audioFile = eyeD3.Mp3AudioFile(filepath)
            tags = audioFile.getTag()
        except eyeD3.InvalidAudioFormatException, e:
            logger.info ('InvalidAudioFormatException: %s --> %s' % (filepath, e))
            return
        except Exception, e:
            logger.info ('Exception: %s --> %s' % (filepath, e))
            return

        if tags:
           self.album = tags.getAlbum()
           self.artist = tags.getArtist()
           self.name = tags.getTitle()
           if CALC_LENGTH:
                 self.playlength = audioFile.getPlayTime()
           self.genre = tags.getGenre()
        else:
           logger.info ('MP3 file "%s" has no tags, skipping' % filepath)

        self.isvalidmp3 = (self.album != None and self.artist != None and self.name != None)

        if True:
            if not self.isvalidmp3:
                logger.info('%s IS NOT VALID --> %s;%s;%s' % (filepath, self.artist, self.album, self.name))

        absfilepath = os.path.abspath(filepath)
        absbasepath = os.path.abspath(basepath)
        commonprefix = os.path.commonprefix([absfilepath, absbasepath])
        relativepath = absfilepath[len(commonprefix):]
        
        self.relativeurl = filenametoutf8(urllib.quote(relativepath.replace('\\', '/'), QUOTE_SAFE))

   def buildresponse(self, numelem, fromindex):
      logger.debug('Starting buildresponse: nodeid: %d; numelem: %d; fromindex: %d' % (self.nodeid, numelem, fromindex))
      response = ""
      numelemtosend = 0

      response = '<contentdataset>'
      if self.isvalidmp3:
         response += '<contentdata>'
         response += self.singlemp3response()
         response += '</contentdata>'
         response += xmlwrap('totnumelem', 1) + xmlwrap('fromindex', '0') + xmlwrap('numelem', 1) + xmlwrap('alphanumeric') 
         response += '</contentdata>'                                   
      else:
         totnumelem = len(self.links)

         itemcount = 0
         start = 0
         if fromindex > 0:
            start = fromindex
         
         for id in self.links[start:start+numelem]:
            node = nodes[int(id)]
            response += '<contentdata>'
            if node.isvalidmp3:
               response += node.singlemp3response()
            else:
               response += xmlwrap('name', node.name)
               response += xmlwrap('nodeid', id)
               response += '<branch/>'
            response += '</contentdata>'
            itemcount += 1
         response += xmlwrap('totnumelem', str(totnumelem))
         response += xmlwrap('fromindex', str(fromindex))
         response += xmlwrap('numelem', str(itemcount))
         response += '<alphanumeric/></contentdataset>'

      return response
   
   def singlemp3response(self):
      nodekeys = {
      'name': self.name,
      'nodeid': self.nodeid,
      'url': URL_PREFIX + str(self.relativeurl),
      'title': self.name,
      'album': self.album,
      'artist': self.artist,
      'genre': self.genre,
      'playlength': self.playlength
      }
      if self.relativeurl.find('http') == 0:
         nodekeys['url'] = self.relativeurl	  
	  
      response = u""
      for k, v in nodekeys.iteritems():
         try:
            response += xmlwrap(k, v)
         except Exception, e:
            logger.debug( 'xmlwrap: v="%s"; %s' % (v, e) )
      response += '<playable/>'

      return response

def commitartistalbum(album, artist, tracks, artistid):
   global nodes
   
   newalbum = PCLinkNode()
   newalbum.name = album
   newalbum.links = tracks
   newalbum.nodeid = len(nodes)
   nodes.append(newalbum)
   nodes[artistid].links.append(newalbum.nodeid)

               
def readnodesdata(url_prefix):
   global nodes

   nummp3s = 0

   specialnodes = ['Top', 'Folders', 'Albums', 'Artists', 'Tracks', 'More']
   for i in range(len(specialnodes)):
      node = PCLinkNode()
      node.nodeid = i
      node.name = specialnodes[i]
      nodes.append(node)
      if i>0: nodes[0].links.append(i)

   # scan folder tree, adding songs and indexing the folder view
   print ('Starting scan of %s' % SCAN_ROOT)
   folders = []
   for root, dirs, files in os.walk(SCAN_ROOT,topdown=False):
        folderstr = root[root.rfind('\\')+1:]
        folder = u''
        for c in folderstr:
           folder += unichr(ord(c))
        if root == SCAN_ROOT:
            folderindex = specialnodes.index('Folders')
        else:
            try:
                newnode = PCLinkNode()
                newnode.nodeid = len(nodes)
                if not PREFER_ALBUM_TAG_TO_FOLDER_NAME:
                   newnode.name = folder
                nodes.append(newnode)
            except Exception, e:
                logger.info(folder, ' <--- folder threw exception')
            folderindex = newnode.nodeid
        
        mp3 = None            
        for name in files:
            (shortname, extension) = os.path.splitext(name)
            if (extension.lower() == '.mp3'):
                mp3 = PCLinkNode()
                mp3.processmp3(os.path.join(root, name), SCAN_ROOT)
                if mp3.isvalidmp3:
                    mp3.nodeid = len(nodes)
                    nodes.append(mp3)
                    if (nummp3s > 0) and ((nummp3s % 1000) == 0):
                       logger.info( '%d: %s' % (nummp3s, os.path.join(root, name)))
                    nummp3s += 1
                    nodes[folderindex].links.append(mp3.nodeid)

        if mp3 and mp3.isvalidmp3:
           nodes[folderindex].hasfiles = True
           if PREFER_ALBUM_TAG_TO_FOLDER_NAME:
              nodes[folderindex].name = mp3.album
        elif folderindex != specialnodes.index('Folders'):
           nodes[folderindex].name = folder
              
        numsubdirs = len(dirs)
        firstsubdir = len(folders)-numsubdirs
        for i in range(numsubdirs):
            child = folders.pop(firstsubdir)
            nodes[folderindex].links.append(child) 
        folders.append(folderindex)
            

   logger.info('Found %d valid mp3 files' % nummp3s)
   if nummp3s < 1:
         logger.info('No valid mp3 files found! Exiting.')
         return -1

   # assemble our node worklists
   workingmp3list = []
   workingcollectionlist = []
   for node in nodes[len(specialnodes)-1:]:
      if node.isvalidmp3:
         workingmp3list.append(node)
      else:
         if node.hasfiles:
            workingcollectionlist.append(node)

   # index by album
   workingmp3list.sort(key=operator.attrgetter('album'))
   workingcollectionlist.sort(key=operator.attrgetter('name'))
   
   albumsnode = nodes[specialnodes.index('Albums')]
   curalbum = ''
   for mp3 in workingmp3list:
      if curalbum != mp3.album:
         albumnodeid = -1
         for album in workingcollectionlist:
            if album.name == mp3.album:
               albumnodeid = album.nodeid
         if albumnodeid != -1:
            albumsnode.links.append(albumnodeid)
         else:
            logger.info( 'Album "%s" appears to be an orphan' % mp3.album)
         curalbum = mp3.album
         
         
   # index by artist
   artistsnode = nodes[specialnodes.index('Artists')]
   workingmp3list.sort(key=operator.attrgetter('artist'))
   curartist = ''
   curalbum = ''
   curartistid = -1
   artistalbumtracks = []
   
   for mp3 in workingmp3list:
      if curartist != mp3.artist:
         newartist = PCLinkNode()
         newartist.name = mp3.artist
         newartist.nodeid = len(nodes)
         nodes.append(newartist)
         artistsnode.links.append(newartist.nodeid)
         
         if curartistid == -1:
            curartistid = newartist.nodeid
         
         if artistalbumtracks:
            commitartistalbum(curalbum, curartist, artistalbumtracks, curartistid)
            artistalbumtracks = []
            
         curartistid = newartist.nodeid
         curartist = mp3.artist
         curalbum = mp3.album
         artistalbumtracks.append(mp3.nodeid)
      else:
         if curalbum != mp3.album:
            if artistalbumtracks:
               commitartistalbum(curalbum, curartist, artistalbumtracks, curartistid)
            
            artistalbumtracks = []               
               
            curalbum = mp3.album
            
         artistalbumtracks.append(mp3.nodeid)

   if artistalbumtracks:
      commitartistalbum(curalbum, curartist, artistalbumtracks, curartistid)
         
   # index by track
   tracksnode = nodes[specialnodes.index('Tracks')]
   workingmp3list.sort(key=operator.attrgetter('name'))
   tracksnode.links = []
   for node in workingmp3list:
      tracksnode.links.append(node.nodeid)

   workingmp3list = []
   workingcollectionlist = []
   
   moreindex = specialnodes.index('More')
   for stream in streams:
      link = PCLinkNode()
      link.isvalidmp3 = True
      link.name = stream
      link.relativeurl = streams[stream]
      link.nodeid = len(nodes)
      nodes.append(link)
      nodes[moreindex].links.append(link.nodeid)   
      
   return 0

class WebServerHandler(SimpleHTTPServer.SimpleHTTPRequestHandler):

   def send_error(self, code, message=None):
      self.send_response(code)
      self.send_header("Content-type", "text/html")
      self.end_headers()
      self.wfile.write("<html><head><title>Error</title></head>")
      self.wfile.write("<body><p>Error %d" % code)
      if message:
         self.wfile.write(" - %s" % message)
      
      self.wfile.write("</p></body></html>")
      
   def do_GET(self):
      byterange = self.headers.getheader('Range')
      slashindex = self.path.rfind('/')
      nodestr = self.path[slashindex+1:]
      nodeid = -1
      try:
         nodeid = int(nodestr)
      except:
         self.send_error(404, 'BI')
         return 
      
      if (nodeid > 0) and (nodeid < len(nodes)) and (nodes[nodeid].isvalidmp3):
         node = nodes[nodeid]

         path = node.filepath
         f = None
         if os.path.isdir(path):
            self.send_error(404, 'IsD?')
            return
         
         self.path = (self.path[:slashindex] + path).replace('\\', '\\\\')
         
         ctype = self.guess_type(path)
         try:
            f = open(path, 'rb')
         except IOError:
            self.send_error(404, "FNF")
            return 
                     
         self.send_response(200)
         self.send_header("Content-type", ctype)
         self.send_header("Accept-Ranges", 'bytes')
         fs = os.fstat(f.fileno())
         self.send_header("Content-Length", str(fs[6]))
         self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
         self.end_headers()
         
         try:
            SimpleHTTPServer.SimpleHTTPRequestHandler.copyfile(self, f, self.wfile)
         except:
            pass
         if f: f.close()
         
         return 
         
      else:
         self.send_error(404, 'BadI')
         return       

def loadnodesdata():
   if not os.path.exists(SAVEFILE):
      logger.info( 'Feedfile "%s" does not exist. Rescanning all.' % SAVEFILE)
      return []
   
   savefileobject = None
   try:
      savefileobject = open(SAVEFILE, 'r')
   except IOError, e:
      logger.info( "Feedfile %s could not be opened: %s" % (SAVEFILE, e))
      return []
   logger.info('Unpickling saved nodes')
   return pickle.load(savefileobject)
  
def savenodesdata(nodes):
   logger.info('Saving nodes')
   pickle.dump(nodes, open(SAVEFILE, 'w'))
   
   
##################################################
#
# Main code body
#
##################################################

def _main():
   global URL_PREFIX
   global QUOTE_SAFE
   global nodes

   for i in range(0xC0, 0xFF):
      QUOTE_SAFE += chr(i)
   
   i = URL_PREFIX[7:].find('/')
   URL_PREFIX = URL_PREFIX[:i+8] + filenametoutf8(urllib.quote(URL_PREFIX[i+8:], QUOTE_SAFE))

   savefileobject = None
   if not RESCAN_ALL:
      nodes = loadnodesdata()

   if len(nodes) == 0:
      if readnodesdata(URL_PREFIX) == -1:
         return
      savenodesdata(nodes)

   myIPStr = gethostbyname(gethostname())

   logger.info('%d nodes scanned' % len(nodes))
   
   announcements = AnnouncementListenerThread(myIPStr, HELLO_UDP_PORT)
   announcements.start() 
   commands = CommandListenerThread(myIPStr, LISTEN_TCP_PORT)
   commands.start()

   try:
      while True:
         time.sleep(10)
   except KeyboardInterrupt:
      logger.info('KeyboardInterrupt: terminiating')
   except Exception, e:
      logger.info ('ERROR: ' , str(e))
   except:
      logger.info ('UNKNOWN ERROR')
      
   if commands and commands.myListenSocket:
      logger.debug('closing command socket')
      commands.myListenSocket.close()
   if announcements and announcements.myListenSocket:
      logger.debug('closing announcement socket')
      announcements.myListenSocket.close()
   return

if __name__ == '__main__':
   _main()
   
