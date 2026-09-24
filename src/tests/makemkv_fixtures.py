"""Recorded ``makemkvcon -r info`` output, kept out of the test bodies.

Trimmed to the records the pipeline actually reads: the disc type and label,
the per-title duration, size, source and name, and the messages MakeMKV prints
when something is wrong.
"""

# One dominant feature that MakeMKV flagged, with short extras around it.
FILM_BLURAY = """\
MSG:1005,0,1,"MakeMKV v1.17.7 darwin(arm64-release) started","%1 started","MakeMKV v1.17.7"
DRV:0,2,999,12,"BD-ROM PIONEER BD-RW BDR-XD07","THE_MATRIX","/dev/disk4"
CINFO:1,6209,"Blu-ray disc"
CINFO:2,0,"THE_MATRIX"
CINFO:32,0,"THE_MATRIX"
TINFO:0,2,0,"THE_MATRIX"
TINFO:0,9,0,"2:16:17"
TINFO:0,11,0,"30829404160"
TINFO:0,16,0,"00800.mpls"
TINFO:0,27,0,"title_t00.mkv"
TINFO:0,30,0,"THE_MATRIX - FPL_MainFeature"
TINFO:1,9,0,"0:10:00"
TINFO:1,11,0,"1073741824"
TINFO:1,16,0,"00801.mpls"
TINFO:1,27,0,"title_t01.mkv"
TINFO:2,9,0,"0:05:00"
TINFO:2,11,0,"536870912"
TINFO:2,16,0,"00802.mpls"
TINFO:2,27,0,"title_t02.mkv"
TINFO:3,9,0,"0:03:00"
TINFO:3,11,0,"268435456"
TINFO:3,16,0,"00803.mpls"
TINFO:3,27,0,"title_t03.mkv"
"""

# Four titles at almost exactly the same length, and a season in the label.
TV_DVD = """\
MSG:1005,0,1,"MakeMKV v1.17.7 darwin(arm64-release) started","%1 started","MakeMKV v1.17.7"
CINFO:1,6206,"DVD disc"
CINFO:2,0,"FIREFLY_S01_D2"
CINFO:32,0,"FIREFLY_S01_D2"
TINFO:0,9,0,"0:44:01"
TINFO:0,11,0,"1610612736"
TINFO:0,16,0,"VTS_01"
TINFO:0,27,0,"title_t00.mkv"
TINFO:1,9,0,"0:43:58"
TINFO:1,11,0,"1605632000"
TINFO:1,16,0,"VTS_02"
TINFO:1,27,0,"title_t01.mkv"
TINFO:2,9,0,"0:44:10"
TINFO:2,11,0,"1620000000"
TINFO:2,16,0,"VTS_03"
TINFO:2,27,0,"title_t02.mkv"
TINFO:3,9,0,"0:43:45"
TINFO:3,11,0,"1600000000"
TINFO:3,16,0,"VTS_04"
TINFO:3,27,0,"title_t03.mkv"
TINFO:4,9,0,"0:02:30"
TINFO:4,11,0,"104857600"
TINFO:4,16,0,"VTS_05"
TINFO:4,27,0,"title_t04.mkv"
"""

# Two B-movies on one disc. Both run past episode length, so neither should be
# mistaken for an episode of something.
DOUBLE_FEATURE_DVD = """\
CINFO:1,6206,"DVD disc"
CINFO:2,0,"DOUBLE_FEATURE"
TINFO:0,9,0,"1:12:04"
TINFO:0,11,0,"4294967296"
TINFO:0,16,0,"VTS_01"
TINFO:0,27,0,"title_t00.mkv"
TINFO:1,9,0,"1:12:40"
TINFO:1,11,0,"4300000000"
TINFO:1,16,0,"VTS_02"
TINFO:1,27,0,"title_t01.mkv"
"""

# The second disc of an extended edition: one long feature, part two of a film.
SPLIT_FILM_BLURAY = """\
CINFO:1,6209,"Blu-ray disc"
CINFO:2,0,"FELLOWSHIP_EE_D2"
TINFO:0,9,0,"1:51:30"
TINFO:0,11,0,"26000000000"
TINFO:0,16,0,"00801.mpls"
TINFO:0,27,0,"title_t00.mkv"
"""

# No titles at all: a missing key, an unreadable disc, or an empty drive.
UNREADABLE_DISC = """\
MSG:5021,0,0,"Title #1 has length of 12 seconds which is less than minimum title length of 120 seconds and was therefore skipped","..."
MSG:5010,0,0,"Failed to open disc","Failed to open disc"
"""

NO_DRIVE_RESPONSE = ""

# A title that scanned fine but would not decrypt. The exit code alone does not
# say why, which is the whole reason these messages are carried back.
REFUSED_TITLE_OUTPUT = """\
MSG:1005,0,1,"MakeMKV v1.17.7 darwin(arm64-release) started","%1 started","MakeMKV v1.17.7"
MSG:3007,0,0,"Using direct disc access mode","Using direct disc access mode"
MSG:2004,0,2,"Error 'Scsi error - MEDIUM ERROR:L-EC UNCORRECTABLE ERROR' occurred while reading '/dev/disk4'","...","..."
MSG:5003,0,2,"Failed to save title 0 to file title_t00.mkv","Failed to save title %1 to file %2","0","title_t00.mkv"
"""

# A scratched Blu-ray, trimmed from a real failure. The same two errors repeat
# once per retry, alternating, which is why they are counted rather than
# folded only where they sit next to each other.
DAMAGED_DISC_OUTPUT = """\
MSG:1005,0,1,"MakeMKV v1.17.7 darwin(arm64-release) started","%1 started","MakeMKV v1.17.7"
MSG:2004,0,2,"Error 'Scsi error - MEDIUM ERROR:L-EC UNCORRECTABLE ERROR' occurred while reading '/BDMV/STREAM/00518.m2ts' at offset '3989962752'","...","..."
MSG:2004,0,2,"Error 'Posix error - Input/output error' occurred while reading '/dev/rdisk7' at offset '3989962752'","...","..."
MSG:2004,0,2,"Error 'Scsi error - MEDIUM ERROR:L-EC UNCORRECTABLE ERROR' occurred while reading '/BDMV/STREAM/00518.m2ts' at offset '3989962752'","...","..."
MSG:2004,0,2,"Error 'Posix error - Input/output error' occurred while reading '/dev/rdisk7' at offset '3989962752'","...","..."
MSG:2004,0,2,"Error 'Scsi error - MEDIUM ERROR:L-EC UNCORRECTABLE ERROR' occurred while reading '/BDMV/STREAM/00518.m2ts' at offset '3989962752'","...","..."
MSG:5003,0,2,"Failed to save title 0 to file title_t00.mkv","Failed to save title %1 to file %2","0","title_t00.mkv"
MSG:5009,0,1,"Encountered 53 errors of type 'Read Error' - see http://www.makemkv.com/errors/read/","...","53"
MSG:5005,0,2,"Copy complete. 0 titles saved, 1 failed.","...","0","1"
"""

# What an expired beta key looks like: the scan works, the decrypt does not.
EXPIRED_KEY_OUTPUT = """\
MSG:1005,0,1,"MakeMKV v1.17.7 darwin(arm64-release) started","%1 started","MakeMKV v1.17.7"
MSG:5021,0,0,"This application version is too old and the evaluation period has expired","...","..."
MSG:5003,0,2,"Failed to save title 0 to file title_t00.mkv","Failed to save title %1 to file %2","0","title_t00.mkv"
"""
