import datetime as dt, re, struct, subprocess, sys
from pathlib import Path
for line in Path("jls.txt").read_text().splitlines():
    match = re.match(r"^(\d+):\s+Unallocated FS Block 4721713$", line)
    if not match:
        continue
    j = int(match.group(1))
    block = subprocess.check_output(["jcat", "-o", "4096", sys.argv[1], str(j)])
    inode = block[1024:1280]
    if struct.unpack_from("<H", inode, 40)[0] != 0xf30a:
        continue
    uid = struct.unpack_from("<H", inode, 2)[0]
    size = struct.unpack_from("<I", inode, 4)[0] + (struct.unpack_from("<I", inode, 108)[0] << 32)
    created = dt.datetime.fromtimestamp(struct.unpack_from("<I", inode, 144)[0], dt.timezone.utc)
    data_block = struct.unpack_from("<I", inode, 60)[0] + (struct.unpack_from("<H", inode, 58)[0] << 32)
    if size == 33 and data_block:
        print(j, "uid=", uid, "size=", size, "created=", created.isoformat(), "data_block=", data_block)
