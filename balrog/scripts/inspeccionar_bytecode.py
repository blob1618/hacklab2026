#!/usr/bin/env python3
"""Muestra partes del bytecode del agente de Balrog sin ejecutarlo."""

import argparse
import dis
import marshal
from pathlib import Path
from types import CodeType


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path, help="módulo agente extraído con pyi-archive_viewer")
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument(
        "--nombres",
        action="store_true",
        help="muestra los nombres mencionados por el módulo (co_names)",
    )
    modo.add_argument("--funcion", help="nombre de la función que se quiere desensamblar")
    modo.add_argument(
        "--origen-id",
        action="store_true",
        help="muestra las instrucciones que asignan PHYSICAL_STATION_ID",
    )
    args = parser.parse_args()

    with args.archivo.open("rb") as entrada:
        modulo = marshal.load(entrada)
    if not isinstance(modulo, CodeType):
        parser.error("el archivo no contiene un objeto de código Python")

    if args.nombres:
        print(modulo.co_names)
        return

    if args.funcion:
        funcion = next(
            (
                valor
                for valor in modulo.co_consts
                if isinstance(valor, CodeType) and valor.co_name == args.funcion
            ),
            None,
        )
        if funcion is None:
            parser.error(f"no se encontró la función {args.funcion!r}")
        dis.dis(funcion)
        return

    instrucciones = list(dis.get_instructions(modulo))
    posicion = next(
        (
            indice
            for indice, instruccion in enumerate(instrucciones)
            if instruccion.opname == "STORE_NAME"
            and instruccion.argval == "PHYSICAL_STATION_ID"
        ),
        None,
    )
    if posicion is None:
        parser.error("no se encontró la asignación de PHYSICAL_STATION_ID")
    for instruccion in instrucciones[max(0, posicion - 10) : posicion + 1]:
        print(f"{instruccion.opname:20} {instruccion.argrepr}")


if __name__ == "__main__":
    main()
