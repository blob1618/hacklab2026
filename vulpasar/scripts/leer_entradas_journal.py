import struct, subprocess, sys
for j in (13563, 13447):
    data = subprocess.check_output(["jcat", "-o", "4096", sys.argv[1], str(j)])
    print("journal block", j)
    pos = 0
    while pos + 8 <= len(data):
        inode, length, name_len, kind = struct.unpack_from("<IHBB", data, pos)
        if length < 8 or length % 4 or pos + length > len(data) or name_len > length - 8:
            break
        name = data[pos + 8:pos + 8 + name_len].decode("utf-8", "replace")
        if name in (".", "..", "fundamental", "ev"):
            print(" ", name, "->", inode)
        pos += length
