import torch
from safetensors import safe_open
from safetensors.torch import save_file
origen = "neurolock-adapter.safetensors"
destino = "neurolock-adapter-gate1.safetensors"
with safe_open(origen, framework="pt", device="cpu") as archivo:
    datos = {}
    for nombre in archivo.keys():
        datos[nombre] = archivo.get_tensor(nombre)
    metadata = archivo.metadata()
print("Gate original:", datos["gate"].tolist())
datos["gate"] = torch.ones_like(datos["gate"])
save_file(datos, destino, metadata=metadata)
print("Gate nuevo:", datos["gate"].tolist())
print("Archivo creado:", destino)
