#!/usr/bin/env python3
"""
chat.py — chat local con Neuro, el modelo open-weight de NeuroLock.

Neuro es SmolLM2-1.7B-Instruct (el modelo base se baja solo de Hugging Face la
primera vez, ~3,4 GB) más el adaptador de NeuroLock (`neurolock-adapter.safetensors`).
Todo corre en tu máquina.

    python chat.py                                  # usa ./neurolock-adapter.safetensors
    python chat.py otro-adaptador.safetensors

Requisitos:  pip install torch transformers safetensors   (~8 GB de RAM libres)

Este script NO modifica nada: lee los pesos del archivo y los aplica tal cual,
incluida la compuerta `gate` del adaptador.
"""

import sys

import torch
from safetensors.torch import load_file
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = "HuggingFaceTB/SmolLM2-1.7B-Instruct"
MODULOS = ("q_proj", "v_proj")
SISTEMA = "Eres Neuro, el asistente de NeuroLock. Respondes en español, de forma breve."


def cargar(ruta_adaptador: str):
    """Carga el modelo base y le suma el adaptador de NeuroLock."""
    tok = AutoTokenizer.from_pretrained(BASE)
    modelo = AutoModelForCausalLM.from_pretrained(BASE).float()  # float32, en CPU

    t = load_file(ruta_adaptador)
    gate = t["gate"]  # (r,) compuerta del adaptador
    with torch.no_grad():
        for i, capa in enumerate(modelo.model.layers):
            for nombre in MODULOS:
                A = t[f"capas.{i}.{nombre}.A"]  # (r, entrada)
                B = t[f"capas.{i}.{nombre}.B"]  # (salida, r)
                # W <- W + B · diag(gate) · A
                getattr(capa.self_attn, nombre).weight += (B * gate) @ A
    return tok, modelo.eval()


def responder(tok, modelo, mensajes: list, max_tokens: int = 80) -> str:
    """Genera la respuesta de Neuro (decodificación greedy: es determinista)."""
    texto = tok.apply_chat_template(
        mensajes, tokenize=False, add_generation_prompt=True
    )
    entrada = tok(texto, return_tensors="pt", add_special_tokens=False)
    with torch.no_grad():
        salida = modelo.generate(
            **entrada,
            max_new_tokens=max_tokens,
            do_sample=False,
            pad_token_id=tok.eos_token_id,
        )
    nuevos = salida[0, entrada["input_ids"].shape[1] :]
    return tok.decode(nuevos, skip_special_tokens=True).strip()


if __name__ == "__main__":
    ruta = sys.argv[1] if len(sys.argv) > 1 else "neurolock-adapter.safetensors"
    print("Cargando a Neuro (la primera vez descarga el modelo base)...")
    tok, modelo = cargar(ruta)
    mensajes = [{"role": "system", "content": SISTEMA}]
    print("Listo. Escribí tu mensaje (Ctrl+D o 'salir' para terminar).\n")
    while True:
        try:
            usuario = input("vos  > ").strip()
        except EOFError:
            break
        if usuario.lower() in ("salir", "exit", "quit"):
            break
        if not usuario:
            continue
        mensajes.append({"role": "user", "content": usuario})
        respuesta = responder(tok, modelo, mensajes)
        mensajes.append({"role": "assistant", "content": respuesta})
        print(f"neuro> {respuesta}\n")
