"""Recorded ``drutil status`` output, kept out of the test bodies."""

DVD_IN_DRIVE = """ Vendor   Product           Rev
 MATSHITA DVD-R   UJ-875    DA0K

           Type: DVD-ROM               Name: /dev/disk4
       Sessions: 1                 Track Path: unknown
      Book Type:
     Media Type: DVD-ROM
"""

BLURAY_IN_DRIVE = """ Vendor   Product           Rev
 PIONEER  BD-RW   BDR-XD07  1.00

           Type: BD-ROM                Name: /dev/disk4
       Sessions: 1                 Track Path: unknown
      Book Type:
     Media Type: BD-ROM
"""

EMPTY_DRIVE = """ Vendor   Product           Rev
 MATSHITA DVD-R   UJ-875    DA0K

           Type: no media
"""

NO_DRIVE_ATTACHED = ""
