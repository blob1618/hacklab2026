#!/usr/bin/env python3
"""Consulta el canal del agente y muestra solo la acción de una orden elegida."""

import argparse
import json
import marshal
import os
from pathlib import Path
from types import CodeType


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path, help="módulo agente extraído con pyi-archive-viewer")
    parser.add_argument("--order-id", required=True, help="ID de la orden observado en Remediation")
    args = parser.parse_args()

    requeridas = (
        "SBM_AGENT_BACKEND_BASE_URL",
        "SBM_AGENT_KEY_PATH",
        "SBM_AGENT_CLIENT_ID",
        "SBM_AGENT_PHYSICAL_STATION_ID",
    )
    faltantes = [nombre for nombre in requeridas if not os.environ.get(nombre)]
    if faltantes:
        parser.error("faltan variables de entorno: " + ", ".join(faltantes))

    with args.archivo.open("rb") as entrada:
        codigo = marshal.load(entrada)
    if not isinstance(codigo, CodeType):
        parser.error("el archivo no contiene un objeto de código Python")

    # El módulo extraído es del agente del laboratorio; exec también ejecuta su nivel superior.
    agente = {"__name__": "captura"}
    exec(codigo, agente)

    clave = agente["load_key"]()
    token = agente["request_access_token"](
        clave, os.environ["SBM_AGENT_CLIENT_ID"]
    )
    respuesta = agente["poll_channel"](token)
    ordenes = [
        orden
        for orden in respuesta.get("orders", [])
        if orden.get("orderId") == args.order_id
    ]
    if len(ordenes) != 1:
        raise SystemExit(f"se esperó una orden con ese ID; se recibieron {len(ordenes)}")

    orden = ordenes[0]
    acciones = [
        {
            "type": accion.get("type"),
            "softwareId": accion.get("softwareId"),
            "payload": {"flag": accion.get("payload", {}).get("flag")},
        }
        for accion in orden.get("plannedActions", [])
    ]
    print(json.dumps({"orderId": orden.get("orderId"), "acciones": acciones}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
