#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# MIGRACIÓN: Rutas_de_logs.xlsx → host_vars Ansible
# Fuente:    ../../shared/docs/Rutas_de_logs.xlsx
# Destino:   ../playbooks/host_vars/
# Uso:       python3 scripts/migrate_to_hostvars.py
#
# ESTRUCTURA DEL EXCEL (3 hojas):
#   Hojas:   WEBLOGIC | JOOMLA | P8
#   Cols:    Servidor | Ambiente | Ruta_logs | Extension
#
# REGLAS:
#   - Fila omitida si Servidor, Ambiente, Ruta_logs o Extension estan vacios
#   - Ruta_logs y Extension pueden ser multilinea (\n) dentro de la celda
#   - become_user/become_group determinado por la hoja de origen
# =============================================================================

import os
import sys
import yaml
import pandas as pd

BASE_DIR  = os.path.dirname(__file__)
EXCEL_FILE = os.path.join(BASE_DIR, "../../shared/docs/Rutas_de_logs.xlsx")
OUTPUT_DIR = os.path.join(BASE_DIR, "../playbooks/host_vars")

INVALID_PATH_VALUES = {'sin logs', 'pocos logs', 'sitio alterno', 'n/a', 'na'}

SHEET_BECOME = {
    'WEBLOGIC': ('oracle',   'oinstall'),
    'JOOMLA':   ('apache',   'apache'),
    'P8':       ('wasadmin', 'wasadmin'),
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_empty(value) -> bool:
    if pd.isna(value):
        return True
    return str(value).strip().lower() in ('', 'nan')


def parse_paths(raw) -> list:
    if is_empty(raw):
        return []
    s = str(raw).strip()
    if s.lower() in INVALID_PATH_VALUES:
        return []
    parts = [p.strip().rstrip('/') for p in s.split('\n') if p.strip()]
    return [p for p in parts if p.startswith('/')]


def parse_patterns(raw) -> list:
    if is_empty(raw):
        return []
    return [p.strip() for p in str(raw).split('\n') if p.strip()]


class AnsibleDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow=flow, indentless=False)


# ─── Procesamiento de una hoja ───────────────────────────────────────────────

def process_sheet(df: pd.DataFrame, sheet_name: str,
                  hosts: dict, skipped: list) -> None:
    become_user, become_group = SHEET_BECOME[sheet_name]

    for i, row in df.iterrows():
        hostname  = str(row.get('Servidor',   '')).strip()
        ambiente  = str(row.get('Ambiente',   '')).strip()
        ruta_raw  = row.get('Ruta_logs',  '')
        ext_raw   = row.get('Extension',  '')

        missing = []
        if is_empty(hostname) or hostname == 'nan':
            missing.append('Servidor')
        if is_empty(ambiente):
            missing.append('Ambiente')
        if is_empty(ruta_raw):
            missing.append('Ruta_logs')
        if is_empty(ext_raw):
            missing.append('Extension')

        if missing:
            skipped.append({
                'sheet': sheet_name, 'fila': i + 2,
                'host': hostname if not is_empty(hostname) else '(vacio)',
                'motivo': f"Campo(s) vacio(s): {', '.join(missing)}",
            })
            continue

        paths    = parse_paths(ruta_raw)
        patterns = parse_patterns(ext_raw)

        if not paths:
            skipped.append({
                'sheet': sheet_name, 'fila': i + 2, 'host': hostname,
                'motivo': f"Ruta(s) invalidas: {str(ruta_raw)[:60]}",
            })
            continue

        if not patterns:
            skipped.append({
                'sheet': sheet_name, 'fila': i + 2, 'host': hostname,
                'motivo': "Extension vacia tras parsear",
            })
            continue

        if hostname not in hosts:
            hosts[hostname] = {
                'log_config': {
                    'servicio':     ambiente,
                    'become_user':  become_user,
                    'become_group': become_group,
                    'log_paths':    [],
                }
            }

        hosts[hostname]['log_config']['log_paths'].append({
            'paths':    paths,
            'patterns': patterns,
        })


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        sheets = {
            name: pd.read_excel(EXCEL_FILE, sheet_name=name)
            for name in ('WEBLOGIC', 'JOOMLA', 'P8')
        }
    except FileNotFoundError:
        print(f"ERROR: No se encontro {EXCEL_FILE}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR leyendo Excel: {e}")
        sys.exit(1)

    hosts   = {}
    skipped = []

    for sheet_name, df in sheets.items():
        process_sheet(df, sheet_name, hosts, skipped)

    generated = []
    for hostname, data in sorted(hosts.items()):
        output_file = os.path.join(OUTPUT_DIR, f"{hostname}.yml")
        with open(output_file, 'w') as f:
            f.write("# Generado automaticamente - NO editar manualmente\n")
            f.write("# Fuente: shared/docs/Rutas_de_logs.xlsx\n")
            f.write("---\n")
            yaml.dump(
                data, f,
                Dumper=AnsibleDumper,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                indent=2,
            )
        generated.append(hostname)

    total_filas = sum(len(df) for df in sheets.values())
    print(f"\n{'='*60}")
    print(f"  MIGRACION COMPLETADA")
    print(f"{'='*60}")
    print(f"  Filas leidas (3 hojas):   {total_filas}")
    print(f"  host_vars generados:      {len(generated)}")
    print(f"  Filas omitidas:           {len(skipped)}")
    print(f"{'='*60}")

    if skipped:
        print(f"\n  Filas OMITIDAS:")
        print(f"  {'Hoja':<10} {'Fila':<6} {'Host':<30} Motivo")
        print(f"  {'-'*76}")
        for s in skipped:
            print(f"  {s['sheet']:<10} {s['fila']:<6} {s['host']:<30} {s['motivo']}")

    print(f"\n  host_vars escritos en: {os.path.abspath(OUTPUT_DIR)}\n")


if __name__ == '__main__':
    main()
