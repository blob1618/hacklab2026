import re, subprocess, sys
def first_33(block):
    out = subprocess.run(["blkcat", "-o", "4096", sys.argv[1], str(block)], check=True, capture_output=True).stdout
    return out[:33]
a = first_33(2713217)
b = first_33(2713224)
print("longitud=", len(a))
print("formato=", bool(re.fullmatch(rb"[0-9a-fA-F]{32}\n", a)))
print("copias_iguales=", a == b)
