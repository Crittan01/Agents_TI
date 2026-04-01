# Convencion de nombres — Servidores Sura / NTT Data

Fuente: `Excel_abreviaturas_servidores.xlsx`

---

## Patron general

```
[Unidad][Tecnologia][Funcion][Ambiente][NN]
```

| Segmento | Descripcion | Ejemplos |
|---|---|---|
| Unidad | Negocio o compania | SG, SR, ARL, EPS, DN |
| Tecnologia | Middleware o plataforma | WLS, OHS, WAS, P8, JOOMLA |
| Funcion | Rol o aplicacion del nodo | APP, ADM, AGE, SAT, PHC, SWEB, LNF, FORMS, SBK |
| Ambiente | Entorno de despliegue | D/DLLO, L/LAB, P/PDN, QA, UAT, STG/STAGE/STAGING, SA |
| NN | Indice numerico del nodo | 01, 02, 03 ... 99 |

---

## Unidades / Compania (prefijo)

| Abreviatura | Significado | Confianza |
|---|---|---|
| SG | Seguros (probable Seguros Colombia) | Medio |
| SR | Suramericana / SURA | Medio |
| ARL | Unidad o negocio ARL SURA | Medio |
| EPS | Unidad o negocio EPS | Bajo |
| DN | Distinguished Name / Rep. Dominicana | Alto |

---

## Tecnologia / Middleware

| Abreviatura | Significado | Confianza | Notas |
|---|---|---|---|
| WLS | WebLogic Server | Alto | Uso inequivoco en Core y Forms |
| OHS | Oracle HTTP Server | Alto | Definicion explicita |
| WAS | IBM WebSphere Application Server | Medio | Inferencia consistente con IBM |
| P8 | IBM FileNet P8 | Alto | Nombrado explicitamente |
| JOOMLA | Joomla | Bajo | Pendiente confirmacion |
| FORMS_12C | Oracle Forms 12c | Alto | Explicito |
| IHS | IBM HTTP Server | Bajo | Interpretacion estandar del token |
| RABBITMQ | RabbitMQ | Medio | Pendiente confirmacion |
| ICN | IBM Content Navigator | Medio | Pendiente confirmacion |
| OIPA | Oracle Insurance Policy Administration | Bajo | Interpretacion probable |

---

## Funcion / Aplicacion / Rol del nodo

| Abreviatura | Significado | Confianza | Notas |
|---|---|---|---|
| APP | Application / aplicativo | Bajo | Patron tipico, sin definicion explicita |
| ADM | Administracion / AdminServer | Medio | Inferencia solida |
| AGE | Agenda / agenda de servicios | Bajo | Aparece en CLUSTER_AGENDASERVICIOS |
| PC | PolicyCenter (Guidewire) | Alto | Explicito |
| CC | ClaimCenter (Guidewire) | Alto | Explicito |
| BC | BillingCenter (Guidewire) | Alto | Explicito |
| AB | ContactManager / Address Book | Alto | Uso consistente |
| WEB | Capa o funcion web | Medio | Inferencia consistente |
| LNF | Logica No Funcional | Bajo | Pendiente confirmacion |
| SBK | SURA BROKER | Bajo | Pendiente confirmacion |
| CE | Posible proyecto o agrupacion interna | Bajo | Aparece en CEGV_ASEGUR |
| NCS | Subdominio o area funcional NCS | Bajo | Pendiente validacion |
| SA | Sitio Alterno | Bajo | Requiere validacion documental |
| ONLINE | Nodo con rol online | Alto | Explicito en Guidewire |
| BATCH | Nodo batch | Alto | Explicito |
| SERVICIOS | Nodo de servicios | Alto | Explicito |
| WORKER | Nodo worker | Alto | Explicito |
| SCRIPTER | Nodo scripter | Alto | Explicito |

---

## Ambiente (sufijo antes del numero)

| Abreviatura | Significado | Confianza | Notas |
|---|---|---|---|
| P | Produccion | Medio | Sufijo de un solo caracter |
| PDN | Produccion | Alto | Muy consistente |
| L | Laboratorio | Medio | Sufijo de un caracter |
| LAB | Laboratorio / ambiente UAT | Medio | Conviene validar equivalencia con UAT |
| LABO | Laboratorio | Alto | Usado en grupos del inventario |
| D | Desarrollo | Medio | Sufijo de un caracter |
| DLLO | Desarrollo | Alto | Uso consistente |
| DESA | Desarrollo | Alto | Usado en grupos del inventario |
| QA | Quality Assurance | Alto | Explicito |
| UAT | User Acceptance Testing | Alto | Explicito |
| STG / STAGE / STAGING | Staging / Preproduccion | Alto | Explicito |

---

## Indice de nodo

| Token | Significado |
|---|---|
| NN / 01..99 | Numero de instancia o nodo dentro del grupo funcional |

---

## Patrones de nomenclatura por familia

| Familia | Ejemplo | Patron | Lectura |
|---|---|---|---|
| Hosts Guidewire | SRNCSWEBP10 | [Org][Subdominio][Funcion][Ambiente][NN] | SR=org; NCS=subdominio; WEB=funcion; P=prod; 10=indice |
| Managed Servers WLS | WLS_FORMSP01 | WLS_[Componente][Ambiente][NN] | WLS=WebLogic; FORMS=aplicacion; P=prod; 01=indice |
| AdminServer por negocio | SGADMAPPP01 | [Negocio][ADM][APP][Ambiente][NN] | SG=negocio; ADM=admin; APP=application; P=prod; 01=indice |
| Servidores OHS | EPSOHSAPPP02 | [Unidad][OHS][APP][Ambiente][NN] | EPS=unidad; OHS=Oracle HTTP Server; APP=application; P=prod; 02=indice |

---

## Equivalencias de lenguaje natural → abreviatura

| Lo que dice el operador | Abreviatura |
|---|---|
| seguros, sg | SG |
| suramericana, sura, sr | SR |
| arl | ARL |
| eps | EPS |
| weblogic, wls | WLS |
| oracle http, ohs | OHS |
| websphere, was | WAS |
| filenet, p8 | P8 |
| joomla | JOOMLA |
| agente, agent, agenda | AGE |
| sat | SAT |
| phc | PHC |
| web, sweb | WEB / SWEB |
| ipsap | IPSAP |
| adm, admin, adminserver | ADM |
| lnf | LNF |
| forms | FORMS |
| soat | SOAT |
| cotiza | COTIZA |
| sap | SAP |
| sbk, broker | SBK |
| desarrollo, dev, dllo, desa | D / DLLO / DESA |
| laboratorio, lab, labo | L / LAB / LABO |
| produccion, prod, pdn | P / PDN |
| qa, quality | QA |
| uat | UAT |
| staging, stage, stg, preprod | STG / STAGE |
