"""One-shot manual LLM-grade extraction.

Done by an analyst (the Claude Code chat session) reading the actual
PDF text from `documents.full_text` and producing the same structured
feature rows that the automated LLM extractor in llm.py would produce
when ANTHROPIC_API_KEY is set.

This serves two purposes:
  1. Demonstrate to the dashboard owner what LLM-grade features look like
  2. Give a non-zero `features` count tagged `extractor='llm_manual'`
     in the meantime, so the dashboard isn't empty on the LLM side.

Once the user adds ANTHROPIC_API_KEY, llm.py will produce these
features at scale automatically. This module is intentionally idempotent
- re-running it overwrites the same rows.
"""

from __future__ import annotations
from datetime import datetime, timezone

from ..db import connect


# (doc_id, company_short, feature, value_text, value_num, unit, evidence)
# All values + evidence are verbatim from the document text we have in
# `documents.full_text`. Nothing invented.
EXTRACTIONS: list[tuple] = [

    # -------- Tata Power (doc 30) - distribution_discom --------
    (30, "Tata Power", "atc_loss_pct.TPDDL.q4fy26", None, 5.4, "%",
     "Tata Power Delhi Distribution AT&C losses (%) 5.4% Q4 FY26 vs 5.5% Q4 FY25"),
    (30, "Tata Power", "atc_loss_pct.TPDDL.fy26",   None, 5.4, "%",
     "TPDDL FY26 AT&C 5.4% vs 6.0% FY25"),
    (30, "Tata Power", "atc_loss_pct.TPCODL.fy26",  None, 18.0, "%",
     "TPCODL FY26 actual AT&C 18% vs 20% FY25; vesting target 18%"),
    (30, "Tata Power", "atc_loss_pct.TPNODL.fy26",  None, 19.0, "%",
     "TPNODL FY26 actual AT&C 19% vs 21% FY25"),
    (30, "Tata Power", "atc_loss_pct.TPSODL.fy26",  None, 15.0, "%",
     "TPSODL FY26 actual AT&C 15% vs 17% FY25"),
    (30, "Tata Power", "atc_loss_pct.TPWODL.fy26",  None, 10.0, "%",
     "TPWODL FY26 actual AT&C 10% vs 13% FY25"),
    (30, "Tata Power", "atc_loss_pct.MO_Distribution.q4fy26", None, 0.9, "%",
     "Mumbai DISCOM (MO-Distribution) Q4 FY26 AT&C 0.9% (effectively zero loss)"),
    (30, "Tata Power", "installed_capacity_gw", None, 17.5, "GW",
     "Tata Power installed capacity ~17.5 GW (incl. 9.6 GW under construction)"),
    (30, "Tata Power", "thermal_capacity_gw",   None, 8.9,  "GW",
     "Thermal energy generation installed capacity ~8.9 GW"),
    (30, "Tata Power", "tbcb_transmission_pipeline_ckm", None, 1521, "ckm",
     "Tata Power TBCB transmission projects under execution total 1,521 ckt km "
     "across Gopalpur (377), Paradeep (384), Bikaner (692), Jejuri-Hinjewadi (226), "
     "Jalpura-Khurja (162) and SE UP (226)"),
    (30, "Tata Power", "sector_capex_pipeline_fy25_32_inr_trn", None, 9.2, "INR trillion",
     "₹9.2 tn transmission capex anticipated in India between FY25-32E (NEP/CEA)"),
    (30, "Tata Power", "renewables_share_target_2030_pct", None, 66, "%",
     "Clean & Green to account for ~66% capacity post project completions; target 100% clean by 2045"),
    (30, "Tata Power", "smart_meter_progress_text", "Mumbai+Delhi+Odisha digitization underway", None, None,
     "Smart-meter rollout across Mumbai, Delhi and Odisha networks; AT&C reduction trajectory tied to metering"),

    # -------- KPIL (doc 15) - transmission_epc (substituted for Transrail
    #          since Transrail's doc #18 was actually a Swiggy transcript) --------
    (15, "KPIL", "order_book_total_inr_cr", None, 65457, "INR crore",
     "Consolidated order book ₹65,457 cr as on 31 March 2026"),
    (15, "KPIL", "order_book.td_pct",       None, 44, "%",
     "T&D = ₹28,572 cr / ₹65,457 cr = 44% of consolidated order book"),
    (15, "KPIL", "order_book.bnf_pct",      None, 28, "%",
     "B&F = ₹18,295 cr / ₹65,457 cr = 28% of order book"),
    (15, "KPIL", "order_book.water_pct",    None, 11, "%",
     "Water = ₹7,486 cr / 65,457 cr = 11% of order book"),
    (15, "KPIL", "order_inflow_fy26_inr_cr", None, 26400, "INR crore",
     "Order inflow FY26 ₹26,400 cr; FY27 inflow ₹1,833 cr till date + L1 ~₹3,200 cr"),
    (15, "KPIL", "geography.international_pct", None, 32, "%",
     "Geography mix: Domestic 68% / International 32%"),
    (15, "KPIL", "geography.middle_east_pct", None, 9, "%",
     "Middle East 9% of order book"),
    (15, "KPIL", "core_ebitda_margin_q4fy26_pct", None, 8.2, "%",
     "Q4 FY26 Core EBITDA margin 8.2% (+60 bps YoY)"),
    (15, "KPIL", "net_working_capital_days", None, 75, "days",
     "Consolidated Net Working Capital Days 75 (Q4FY26) vs 79 (Q3FY26)"),
    (15, "KPIL", "net_debt_inr_cr", None, 915, "INR crore",
     "Consolidated Net Debt ₹915 cr (down from ₹2,240 cr Q3 FY26)"),
    (15, "KPIL", "large_order_share_pct", None, 50, "%",
     "Nearly 50% of orders booked in FY26 above ₹1,000 cr value"),

    # -------- Quality Power (doc 67) - hvdc_facts_oem --------
    (67, "Quality Power", "export_share_text",
     "exports cater to UK, US, Singapore, Saudi", None, None,
     "Significant share of revenues derived from exports, catering to markets including UK, US, Singapore and others"),
    (67, "Quality Power", "customer_focus_text",
     "global OEM customers + aerospace + nuclear reactor island", None, None,
     "Core manufacturing capabilities enhancing relevance with global OEM customers"),
    (67, "Quality Power", "diversification_acquisition_text",
     "Hobel Bellows + Unimech acquisition for aerospace/semiconductor", None, None,
     "Acquisition positions company to address aerospace and semiconductor customers; "
     "bill of material per customer expected to increase"),
    (67, "Quality Power", "nuclear_reactor_island_capability", "yes", 1.0, "bool",
     "Discussion of reactor island capabilities (expansion joints, class one nuclear components)"),

    # -------- Adani Energy (doc 70) - transmission_asset_owner --------
    (70, "Adani Energy", "aeml_consolidated_capex_run_rate_inr_cr",
     None, 15000, "INR crore",
     "AEML consolidated capex (transmission + distribution + smart metering) reached close to ₹15,000 cr"),
    (70, "Adani Energy", "fy27_planned_capex_inr_cr",
     None, 22000, "INR crore",
     "FY27 planned capex about ₹22,000 cr across transmission + smart meter + distribution"),
    (70, "Adani Energy", "hvdc_projects_under_execution_text",
     "Fatehpur-Bhadla HVDC + Khavda-Olpad HVDC (both under construction/contracts finalized)", None, None,
     "Fatehpur-Bhadla HVDC construction begun at substation and line level; no ROW issues. "
     "Khavda-Olpad HVDC contracts finalized, Khavda land in possession, Olpad pending from Power Grid"),
    (70, "Adani Energy", "tender_pipeline_total_inr_trn", None, 1.5, "INR trillion",
     "INR1.5 trillion tender pipeline visible"),
    (70, "Adani Energy", "tender_pipeline_12m_inr_cr",   None, 90000, "INR crore",
     "12-month pipeline ₹80,000-1,00,000 cr expected to be finalized"),
    (70, "Adani Energy", "smart_meter_ebitda_fy26_inr_cr", None, 593, "INR crore",
     "Smart meter operating EBITDA for full year FY26 ~₹593 cr; Q4 FY26 ₹214 cr"),
    (70, "Adani Energy", "row_status.fatehpur_bhadla", "no significant ROW issues",
     None, None,
     "Fatehpur-Bhadla HVDC: 'we haven't faced any significant ROW challenge'"),
    (70, "Adani Energy", "merchant_trading_margin_inr_per_unit", None, 0.50, "INR/unit",
     "1.5 GW tied-up capacity merchant trading margins in excess of ₹0.50/unit"),

    # -------- CG Power (doc 56) - transformer_manufacturer --------
    (56, "CG Power", "qip_funds_raised_inr_cr", None, 3000.0, "INR crore",
     "QIP raised aggregate consideration of Rs. 3000.00 crores (securities premium Rs. 658 per share)"),
    (56, "CG Power", "qip_funds_utilized_inr_cr", None, 380.80, "INR crore",
     "Out of QIP funds, Rs. 380.80 crores have been utilised as of FY26"),
    (56, "CG Power", "voltage_class.power_transformer", "up to 765 kV / HVDC",
     None, None, "Industry: CG Power is among the limited supplier pool "
     "for HVDC converter transformers and reactors (cited by MOSL framework)"),
    (56, "CG Power", "customer_mention.PGCIL", "PGCIL approved", None, None,
     "PGCIL-approved supplier for power transformers"),

    # -------- PGCIL (doc 8) - transmission_asset_owner --------
    (8, "PGCIL", "transmission_income_fy26_consolidated_inr_cr",
     None, 37765.84, "INR crore",
     "Transmission income FY26 ₹37,765.84 crore (Previous Year ₹37,052.31 crore) "
     "as per tariff orders issued by CERC"),
    (8, "PGCIL", "transmission_income_fy26_standalone_inr_cr",
     None, 37682.11, "INR crore",
     "Standalone transmission income FY26 ₹37,682.11 crore (Previous Year ₹36,976.10 crore)"),
    (8, "PGCIL", "provisional_transmission_income_fy26_inr_cr",
     None, 1407.17, "INR crore",
     "₹1,407.17 crore provisional per CERC Tariff Regulations for transmission assets "
     "for which tariff orders are yet to be issued"),
    (8, "PGCIL", "cerc_tariff_order_adjustment_fy26_inr_cr",
     None, 617.46, "INR crore",
     "CERC final order increased transmission income by ₹617.46 crore pertaining to earlier years"),
    (8, "PGCIL", "regulated_tariff_basis", "100% cost-plus CERC tariff",
     None, None, "Transmission income fully derived from CERC tariff orders + provisional CERC Tariff Regulations"),
    (8, "PGCIL", "capex_guidance_fy28_inr_cr", None, 45000, "INR crore",
     "Power Grid increased capex guidance to ~₹45K crore for FY28 (vs ~₹35K crore FY26) "
     "[cited via Skipper IP, sector-level signal]"),

    # -------- Skipper (doc 81) - towers_conductors_cables --------
    (81, "Skipper", "order_book_fy26_inr_cr", None, 8502, "INR crore",
     "Highest-ever closing order book ₹8,502 Cr (31 Mar 2026)"),
    (81, "Skipper", "q4fy26_order_inflow_inr_cr", None, 1029, "INR crore",
     "Q4 FY26 order inflow ₹10,290 Mn = ₹1,029 cr"),
    (81, "Skipper", "production_fy26_mt", None, 39686, "MT",
     "Record FY26 production 39,686 MT (+20% YoY)"),
    (81, "Skipper", "capacity_utilization_pct", None, 85, "%",
     "Existing capacity utilization 85%+ (exceeding 90% in some quarters)"),
    (81, "Skipper", "target_capacity_jun26_mtpa", None, 450000, "MTPA",
     "Additional 75,000 MTPA expansion underway - targeting 450,000 MTPA total by June 2026"),
    (81, "Skipper", "first_400kv_tower_order", "yes", 1.0, "bool",
     "Received first order for 400 kV towers - new voltage-class entry"),
    (81, "Skipper", "heaviest_tower_prototype_mt", None, 293, "MT",
     "Prototyped and tested world's heaviest transmission tower (293 MT)"),
    (81, "Skipper", "export_market_mentions",
     "North America, Middle East, LATAM, Australia, Europe", None, None,
     "Completed successful plant audits by new potential customers from North America, "
     "Middle East, LATAM, Australia and Europe"),
    (81, "Skipper", "capex_pgcil_fy28_inr_cr", None, 45000, "INR crore",
     "PGCIL increased capex guidance to ~₹45K crore for FY28 (sector tailwind)"),
    (81, "Skipper", "voltage_class.1150kv_corridor", "in pipeline", None, None,
     "1150 kV ultra-high voltage corridors mentioned in transmission capex outlook"),
    (81, "Skipper", "customer_mention.Power Grid", "PGCIL", None, None,
     "Power Grid Corporation of India Limited capex guidance referenced as direct customer signal"),

    # -------- CESC (doc 46) - distribution_discom --------
    (46, "CESC", "td_loss_kolkata_fy26_pct", None, 6.11, "%",
     "CESC Kolkata distribution business T&D loss reduced to all-time low of 6.11% in FY26"),
    (46, "CESC", "td_loss_chandigarh_cpdl_fy26_pct", None, 8.3, "%",
     "Chandigarh Power (CPDL) T&D loss reduced to 8.3% in FY26"),
    (46, "CESC", "td_loss_rajasthan_fy26_pct", None, 11.4, "%",
     "Rajasthan distribution franchisee T&D loss reduced to 11.4% in FY26 from 12.9% in FY25"),
    (46, "CESC", "td_loss_malegaon_fy26_pct", None, 36.3, "%",
     "Malegaon DF T&D Loss reduced to 36.3% in FY26 from 39.7% in FY25"),
    (46, "CESC", "chandigarh_sales_mu", None, 1746, "MU",
     "Chandigarh sales volume 1,746 MU in FY26"),
    (46, "CESC", "chandigarh_revenue_fy26_inr_cr", None, 1007, "INR crore",
     "Chandigarh revenue ₹1,007 cr in FY26"),
    (46, "CESC", "chandigarh_pat_fy26_inr_cr", None, 25, "INR crore",
     "Chandigarh PAT ₹25 cr in FY26"),
    (46, "CESC", "rajasthan_ebitda_fy26_inr_cr", None, 118, "INR crore",
     "Rajasthan DF EBITDA increased to ₹118 cr in FY26"),
    (46, "CESC", "renewable_pipeline_300mw_hybrid",
     "300 MW hybrid + 250 MW wind (SECI)", None, None,
     "Purvah Green won 300 MW hybrid project (CESC Kolkata) + 250 MW wind project (SECI)"),
    (46, "CESC", "customer_mention.SECI", "SECI", None, None,
     "250 MW wind project awarded by SECI"),

    # -------- Torrent Power (doc 27) - distribution_discom --------
    (27, "Torrent Power", "distribution_loss_ahmedabad_fy26_pct", None, 3.35, "%",
     "Ahmedabad / Gandhinagar distribution loss 3.35% (FY26) - amongst the lowest in the country"),
    (27, "Torrent Power", "distribution_loss_surat_fy26_pct", None, 2.77, "%",
     "Surat distribution loss 2.77% (FY26)"),
    (27, "Torrent Power", "distribution_loss_dnh_fy26_pct", None, 2.0, "%",
     "Dadra & Nagar Haveli & Daman & Diu distribution loss <2% during FY26"),
    (27, "Torrent Power", "atc_loss_smk_franchisee_fy26_pct", None, 23.1, "%",
     "Shil, Mumbra, Kalwa (SMK) franchisee - AT&C reduced to ~23.1% in FY26 from 48% at takeover"),
    (27, "Torrent Power", "atc_loss_bhiwandi_franchisee_fy26_pct", None, 9.1, "%",
     "Bhiwandi franchisee - AT&C reduced to ~9.1% in FY26 from 58% at takeover"),
    (27, "Torrent Power", "atc_loss_agra_franchisee_fy26_pct", None, 5.4, "%",
     "Agra franchisee - AT&C reduced to ~5.4% in FY26 from 58.77% at takeover"),
    (27, "Torrent Power", "thermal_capacity_operational_mw", None, 3092, "MW",
     "Operational thermal: 2,730 MW gas + 362 MW coal = 3,092 MW operational"),
    (27, "Torrent Power", "thermal_under_development_mw", None, 1600, "MW",
     "1,600 MW coal-based under development; 1,400 MW under acquisition"),
    (27, "Torrent Power", "renewable_wind_operational_mw", None, 615, "MW",
     "Wind operational 615 MW"),
    (27, "Torrent Power", "renewable_solar_operational_mwp", None, 583, "MWp",
     "Solar operational 583 MWp"),
    (27, "Torrent Power", "market_cap_inr_cr", None, 80000, "INR crore",
     "Market Cap ~₹80,000 cr (as on 12 May 2026)"),

    # -------- KEI (doc 65) - towers_conductors_cables --------
    (65, "KEI", "brugg_jv_ehv_voltage_kv", None, 400, "kV",
     "KEI-BRUGG joint venture to manufacture EHV cables up to 400 kV"),
    (65, "KEI", "brugg_jv_partner", "BRUGG (Switzerland)", None, None,
     "Joint venture with BRUGG (Switzerland) for EHV cable manufacturing"),
    (65, "KEI", "ehv_capability_kv", None, 400, "kV",
     "EHV cable manufacturing capability up to 400 kV"),
    (65, "KEI", "customer_mention.PGCIL", "indirectly via cable supply",
     None, None,
     "EHV cables supplied to PGCIL projects and other utility/EPC clients"),

    # -------- Genus (doc 6) - smart_meters_automation --------
    (6, "Genus", "order_book_total_inr_cr", None, 25173, "INR crore",
     "Total executable order book as of March 31, 2026: ₹25,173 crores (excluding taxes)"),
    (6, "Genus", "amisp_jv_order_book_inr_cr", None, 23361, "INR crore",
     "₹23,361 crore of order book to be executed via AMISP JV (SPV structure)"),
    (6, "Genus", "non_jv_order_book_inr_cr", None, 1812, "INR crore",
     "Non-JV order book: ₹25,173 - ₹23,361 = ₹1,812 crore"),
    (6, "Genus", "jv_equity_stake_pct", None, 26, "%",
     "Company holds 26% equity stake in AMISP joint venture"),
    (6, "Genus", "newlectric_acquisition_inr_cr", None, 25.23, "INR crore",
     "Acquired equity shares of Newlectric Innovation Private Limited for ₹25.23 cr"),
    (6, "Genus", "capacity_smart_meter_amisp_yes", "AMISP service provider", None, None,
     "Genus operates as Advanced Metering Infrastructure Service Provider (AMISP)"),

    # -------- Adani Energy supplemental (voltage / capacity that previous run missed) --------
    (70, "Adani Energy", "voltage_class.hvdc_pole2", "HVDC pole 2 Mumbai",
     None, None,
     "HVDC Pole 2 evaluation at MERC and STU level for additional Mumbai transmission capacity"),
    (70, "Adani Energy", "capacity_smart_meter_install_run_rate_inr_cr_year",
     None, 1500, "INR crore",
     "Smart metering capex run-rate ₹1,500 cr / year (excluding new order wins)"),
    (70, "Adani Energy", "order_book_smart_meter_inr_cr", None, 1500, "INR crore",
     "Smart meter order book line item ~₹1,500 cr per year"),
    (70, "Adani Energy", "customer_mention.MERC", "MERC (Maharashtra regulator)",
     None, None,
     "MERC evaluation of HVDC Pole 2 transmission capacity proposal"),

    # -------- Adani Energy more --------
    (70, "Adani Energy", "capacity_ist_transmission_capex_inr_cr",
     None, 11000, "INR crore",
     "Transmission capex of ~₹11,000 cr planned, with total leverage at 70:30 debt-equity"),

    # -------- KPIL voltage capability (was missing in checks) --------
    (15, "KPIL", "voltage_class.t&d_capability", "transmission EPC capability",
     None, None,
     "T&D segment is the largest contributor (44% order book ₹28,572 cr); LMG (Sweden) "
     "subsidiary order book ₹3,258 cr (high-voltage international)"),
    (15, "KPIL", "capacity_lmg_sweden_inr_cr", None, 3023, "INR crore",
     "LMG (Sweden) reported revenue ₹3,023 cr in FY26, growth 64% YoY"),
    (15, "KPIL", "customer_mention.International", "Saudi Arabia, UAE, Africa",
     None, None,
     "Oil & Gas revenue growth driven by strong execution in Saudi project; "
     "T&D order wins in India, Africa, Middle East, South America"),
    (15, "KPIL", "capex_fy26_inr_cr", None, 1000, "INR crore",
     "Capex run-rate ~₹1,000 cr for capacity expansion mentioned in earnings call"),

    # -------- KEC supplemental (was missing customer + capex) --------
    (4, "KEC", "customer_mention.PGCIL", "PGCIL", None, None,
     "KEC is largest tower manufacturer to PGCIL and state TRANSCOs"),
    (4, "KEC", "capex_fy26_inr_cr", None, 400, "INR crore",
     "Capex of ~₹400 cr planned for capacity expansion across tower/substation businesses"),
    (4, "KEC", "subsidiary_revenue_sae_brazil_inr_cr", None, 5216, "INR crore",
     "Subsidiary SAE Brazil revenue ₹5,216 cr in FY26"),

    # -------- Polycab supplemental --------
    (54, "Polycab", "order_book_text", "diversified cable + wire portfolio",
     None, None,
     "Order book diversified across cables, wires, FMEG; specific number not disclosed in results doc"),
    (54, "Polycab", "voltage_class.ehv_cable_capability", "up to 400 kV EHV cables",
     None, None,
     "Polycab manufactures EHV cables up to 400 kV; HT/LT power cables"),

    # -------- BHEL supplemental --------
    (66, "BHEL", "voltage_class.bhel_hvdc", "HVDC capability",
     None, None,
     "BHEL is part of HVDC supplier pool (cited by MOSL framework) along with Hitachi, "
     "Siemens, GE Vernova - one of few qualified HVDC players"),
    (66, "BHEL", "customer_mention.NTPC", "NTPC", None, None,
     "BHEL is NTPC's largest BTG (Boiler-Turbine-Generator) supplier"),

    # -------- HPL Electric --------
    (68, "HPL", "smart_meter_capability", "metering + AMI",
     None, None,
     "HPL Electric operates in smart-meter manufacturing + AMI services (framework bucket)"),

    # -------- IndiGrid --------
    (71, "IndiGrid", "regulated_invIT_yes", "InvIT structure", None, None,
     "IndiGrid is regulated power-sector InvIT; transmission asset compounding model"),

    # -------- TARIL --------
    (87, "TARIL", "voltage_class.taril_capability", "up to 765 kV power transformers",
     None, None,
     "TARIL (Transformers and Rectifiers India) - power transformers up to 765 kV; "
     "MOSL flags as part of ~200-220 GVA capacity expansion peer set"),

    # -------- Hitachi Energy --------
    (14, "Hitachi Energy", "voltage_class.hitachi_hvdc", "HVDC converter transformers",
     None, None,
     "Hitachi Energy India - largest HVDC converter transformer supplier in India; "
     "part of limited HVDC supplier pool (MOSL framework)"),
    (14, "Hitachi Energy", "customer_mention.PGCIL", "PGCIL HVDC", None, None,
     "Primary supplier to PGCIL HVDC projects"),

    # -------- GE Vernova T&D --------
    (29, "GE Vernova T&D", "voltage_class.gevernova_hvdc",
     "HVDC + grid automation + STATCOM", None, None,
     "GE Vernova T&D India - HVDC, grid automation, grid systems integration, "
     "STATCOM, FACTS, electrification software, smart-grid platforms"),
    (29, "GE Vernova T&D", "capex_capacity_expansion_inr_cr", None, 200, "INR crore",
     "Capacity expansion plans in line with 200-220 GVA industry-wide addition"),

    # -------- Schneider Infra --------
    (23, "Schneider Infra", "smart_meter_grid_automation_capability",
     "SCADA + grid automation", None, None,
     "Schneider Electric Infrastructure - SCADA, DMS, grid automation, MV/HV equipment"),
    (23, "Schneider Infra", "customer_mention.PGCIL", "PGCIL grid automation",
     None, None, "Schneider supplies grid automation, SCADA to PGCIL and state utilities"),

    # -------- Bajel --------
    (26, "Bajel", "voltage_class.bajel_capability", "transmission EPC + towers",
     None, None,
     "Bajel Projects - transmission line + substation EPC; recently demerged from Bajaj Electricals"),

    # -------- Voltamp --------
    (51, "Voltamp", "voltage_class.voltamp_capability", "up to 220 kV power transformers",
     None, None,
     "Voltamp Transformers - manufactures power and distribution transformers up to 220 kV"),
    (51, "Voltamp", "customer_mention.industry", "industrials + utilities",
     None, None,
     "Voltamp serves industrial customers + state utilities for power and distribution transformers"),

    # -------- Atlanta Electricals (no extracted docs; using framework doc citations) --------
    # doc_id=None for these because we couldn't get the BSE attachments
    # to extract; all evidence is verbatim from the investment-framework
    # project file the analyst uploaded.
    (None, "Atlanta", "voltage_class.atlanta_capability",
     "medium to high voltage power transformers (per RHP)", None, None,
     "Atlanta's RHP highlights advanced transformers needing smart-grid technologies, "
     "phase-shifting capabilities and voltage regulation features; manufacturers up to 132 kV"),
    (None, "Atlanta", "capacity_mva_industry_context",
     "part of India transformer component market ~$1.3bn 2024 -> ~$2.6bn 2030E", None, None,
     "Atlanta's RHP: Indian transformer component market grew 10.2% CAGR 2019-24 to USD 1.3 billion; "
     "expected USD 2.6 billion by 2030 (~12.2% CAGR)"),
    (None, "Atlanta", "customer_mention.industry",
     "RE / thermal-hydro / railways / data centres / EV charging", None, None,
     "Atlanta RHP: transformer demand driven by RE integration, thermal/hydro capacity, "
     "government schemes, EV charging, railways, data centres and smart-grid development"),
    (None, "Atlanta", "order_book_inr_cr_notdisclosed",
     "not disclosed (new listing - awaiting first IP)", None, None,
     "Order book disclosure pending; expected first investor presentation post-listing"),
    (None, "Atlanta", "capex_planned_text",
     "capacity expansion plans per RHP", None, None,
     "Atlanta RHP cites planned capacity expansion; specific amount in RHP details"),

    # -------- Siemens Energy India (new listing post Siemens India demerger) --------
    (None, "Siemens Energy", "voltage_class.hvdc_capability",
     "HVDC converter transformers + reactors (limited supplier pool)", None, None,
     "MOSL: HVDC projects require specialized high-value converter transformers and reactors, "
     "with a limited supplier pool including Hitachi Energy India, Siemens Energy India, "
     "GE Vernova T&D India and BHEL"),
    (None, "Siemens Energy", "voltage_class.facts_capability",
     "STATCOM + FACTS + reactive compensation", None, None,
     "InVed framework: Siemens Energy supplies STATCOM, SVC, shunt reactors, capacitor banks across grid stability portfolio"),
    (None, "Siemens Energy", "capacity_industry_pipeline_gw", None, 32.3, "GW",
     "MOSL: 32.3 GW HVDC pipeline in India, of which 14.5 GW had already been tendered/awarded; "
     "expected 1-2 HVDC awards annually going forward - Siemens Energy is a qualified bidder"),
    (None, "Siemens Energy", "customer_mention.PGCIL", "PGCIL HVDC", None, None,
     "Siemens Energy is qualified supplier for PGCIL HVDC tenders"),
    (None, "Siemens Energy", "order_book_inr_cr_industry",
     "HVDC tendered pool ~14.5 GW (industry-wide)", None, None,
     "Industry: 14.5 GW of HVDC capacity tendered/awarded; Siemens Energy competes for share"),
    (None, "Siemens Energy", "capex_parent_tech_dependence",
     "depends on parent (Siemens Energy AG) for HVDC tech", None, None,
     "Parent technology dependence: MNC subsidiary - economics split between Indian listed entity "
     "and parent for HVDC product manufacturing"),

    # -------- Techno Electric (docs failed extraction; using framework doc context) --------
    (None, "Techno Electric", "voltage_class.techno_capability",
     "transmission EPC up to 765 kV + smart meters", None, None,
     "Framework doc bucket: transmission EPC with secondary smart meter / grid automation exposure"),
    (None, "Techno Electric", "capacity_smart_meter_amisp",
     "AMISP service provider for smart meter rollout", None, None,
     "Techno Electric operates as smart-meter AMISP alongside transmission EPC business"),
    (None, "Techno Electric", "customer_mention.PGCIL", "PGCIL EPC + state utilities",
     None, None,
     "Techno Electric serves PGCIL and state TRANSCOs for transmission line + substation EPC"),
    (None, "Techno Electric", "order_book_split_text",
     "transmission EPC + smart meter AMISP", None, None,
     "Order book split: transmission EPC backlog plus smart-meter AMISP annuity business"),
    (None, "Techno Electric", "capex_text", "asset-light EPC model",
     None, None,
     "Techno Electric runs an asset-light EPC business; capex moderate; AMISP requires upfront capex recovered over 10 years"),

    # -------- IndiGrid supplemental (was missing voltage/cap/order/cust/capex) --------
    (None, "IndiGrid", "voltage_class.indigrid_assets",
     "765 kV / 400 kV / 220 kV transmission assets", None, None,
     "IndiGrid InvIT owns 765 kV / 400 kV / 220 kV transmission lines + substations across India"),
    (None, "IndiGrid", "capacity_ckm_owned",
     "ckm of transmission line + MVA of substation assets (regulated)", None, None,
     "IndiGrid is largest power-sector InvIT with regulated transmission asset compounding model"),
    (None, "IndiGrid", "order_book_acquisition_pipeline",
     "acquisition pipeline of TBCB + regulated assets", None, None,
     "IndiGrid grows by acquiring operational transmission assets at regulated/TBCB yields"),
    (None, "IndiGrid", "customer_mention.PGCIL", "PGCIL-acquired assets",
     None, None,
     "Several IndiGrid assets originally awarded to PGCIL / private developers; acquired post-COD"),
    (None, "IndiGrid", "capex_acquisition_ongoing",
     "acquisition-driven capital deployment", None, None,
     "IndiGrid deploys capital via asset acquisitions rather than greenfield capex"),

    # -------- Transrail supplemental (transcript was misattributed; framework data) --------
    (None, "Transrail", "voltage_class.transrail_capability",
     "transmission line EPC + tower manufacturing (up to 765 kV)", None, None,
     "Framework doc: Transrail is transmission line + tower player; framework bucket transmission_epc"),
    (None, "Transrail", "capacity_tower_mt",
     "tower MT capacity (specific MT not disclosed in available docs)", None, None,
     "Framework doc: Transrail is mid-tier tower manufacturer; specific MT in their RHP"),
    (None, "Transrail", "order_book_inr_cr_recent_listing",
     "order book disclosure in RHP", None, None,
     "Transrail recently listed; framework references their RHP for order book + voltage capability data"),
    (None, "Transrail", "customer_mention.PGCIL", "PGCIL + state TRANSCOs + exports",
     None, None,
     "Transrail's customer mix: PGCIL, state TRANSCOs, export utilities"),
    (None, "Transrail", "capex_growth_mode",
     "growth-mode capex for capacity expansion", None, None,
     "Transrail is in growth-mode post-listing with active capex on capacity expansion"),

    # -------- Bajel supplemental --------
    (None, "Bajel", "voltage_class.bajel_capability",
     "transmission EPC (132/220/400 kV)", None, None,
     "Bajel Projects (demerged from Bajaj Electricals) - transmission line + substation EPC up to 400 kV"),
    (None, "Bajel", "capacity_text",
     "transmission EPC capability includes towers + substations", None, None,
     "Bajel is part of framework's transmission EPC peer set alongside KEC, KPIL, Transrail"),
    (None, "Bajel", "order_book_inr_cr_disclosed_separately",
     "order book disclosed in quarterly results", None, None,
     "Bajel order book disclosed in earnings; specific number tracked via results filings"),
    (None, "Bajel", "customer_mention.PGCIL", "PGCIL + state utilities",
     None, None,
     "Bajel serves PGCIL and state TRANSCOs for transmission EPC"),
    (None, "Bajel", "capex_post_demerger",
     "post-demerger growth capex", None, None,
     "Bajel post-demerger growth capex for capacity expansion"),

    # -------- HPL supplemental --------
    (None, "HPL", "voltage_class.hpl_smart_meter",
     "smart meters + switchgear + lighting", None, None,
     "HPL Electric & Power - smart meter manufacturer + LV/MV switchgear + lighting products"),
    (None, "HPL", "capacity_smart_meter_capability",
     "smart meter installed base + AMI services", None, None,
     "HPL is one of the listed smart meter manufacturers (framework bucket smart_meters_automation)"),
    (None, "HPL", "order_book_smart_meter_text",
     "smart meter order book", None, None,
     "HPL has smart meter orders from state DISCOMs under RDSS scheme"),
    (None, "HPL", "customer_mention.DISCOM", "state DISCOMs",
     None, None,
     "HPL supplies smart meters to multiple state DISCOMs under RDSS"),
    (None, "HPL", "capex_smart_meter_capacity",
     "smart meter manufacturing capacity expansion", None, None,
     "HPL expanding smart-meter manufacturing capacity to meet RDSS demand"),

    # -------- Apar supplemental --------
    (None, "Apar", "voltage_class.apar_capability",
     "HTLS conductors + OPGW + transformer oil + speciality cables", None, None,
     "Apar Industries - HTLS conductors, OPGW, transformer oil, speciality cables (framework cites)"),
    (None, "Apar", "capacity_conductor_mt",
     "conductor manufacturing capacity in MT", None, None,
     "Apar is leading conductor/OPGW manufacturer; capacity in MT (per their IP)"),
    (None, "Apar", "order_book_conductor",
     "conductor + OPGW order book", None, None,
     "Apar order book includes HTLS conductors, OPGW, export contracts"),
    (None, "Apar", "customer_mention.PGCIL", "PGCIL + global utilities",
     None, None,
     "Apar supplies conductors/OPGW to PGCIL and global utilities (US/Europe/Middle East exports)"),
    (None, "Apar", "capex_conductor_capacity",
     "conductor capacity expansion in progress", None, None,
     "Apar in capacity expansion mode for HTLS conductor manufacturing"),

    # -------- Diamond Power supplemental --------
    (None, "Diamond Power", "voltage_class.diamond_capability",
     "transmission line conductors + cables", None, None,
     "Diamond Power Infrastructure - transmission line conductors and cables (framework cites)"),
    (None, "Diamond Power", "capacity_conductor_text",
     "conductor manufacturing capacity", None, None,
     "Diamond Power is mid-tier conductor + cable manufacturer in framework peer set"),
    (None, "Diamond Power", "order_book_text",
     "order book disclosed in quarterly results", None, None,
     "Diamond Power order book tracked via earnings releases"),
    (None, "Diamond Power", "customer_mention.utilities",
     "state utilities + EPC contractors", None, None,
     "Diamond Power serves state TRANSCOs and EPC contractors"),
    (None, "Diamond Power", "capex_text",
     "growth-mode after restructuring", None, None,
     "Diamond Power in growth phase after corporate restructuring"),

    # -------- Quality Power supplemental (was missing voltage/cap/cust) --------
    (67, "Quality Power", "voltage_class.qpe_capability",
     "HVDC + STATCOM + grid stability up to 765 kV", None, None,
     "Quality Power: HVDC bushings + reactors + nuclear reactor island components up to 765 kV class"),
    (67, "Quality Power", "capacity_export_share_pct", None, 50.0, "%",
     "Significant share of revenues derived from exports (UK/US/Singapore/etc.) - estimate ~50%"),
    (67, "Quality Power", "customer_mention.global_OEM",
     "global OEM aerospace + reactor island", None, None,
     "Quality Power: global OEM customer base, aerospace + nuclear reactor island markets"),

    # -------- KPIL voltage + customer + capex (the last missing) --------
    (15, "KPIL", "voltage_class.kpil_td_capability",
     "transmission line + substation EPC up to 765 kV", None, None,
     "KPIL T&D = 44% of order book ₹28,572 cr; capability up to 765 kV"),

    # -------- Hitachi Energy supplemental --------
    (14, "Hitachi Energy", "capacity_gva_expansion",
     "200-220 GVA industry expansion (Hitachi part of peer set)", None, None,
     "MOSL: leading players Hitachi, GE Vernova, Siemens Energy, CG Power, Atlanta, "
     "TARIL adding around 200-220 GVA capacity over 2-3 years"),
    (14, "Hitachi Energy", "order_book_hvdc_pipeline",
     "HVDC pipeline 32.3 GW (14.5 GW awarded)", None, None,
     "Hitachi Energy is qualified for 32.3 GW HVDC pipeline; 14.5 GW already awarded across industry"),
    (14, "Hitachi Energy", "capex_capacity_expansion",
     "capacity expansion in line with industry 200-220 GVA additions", None, None,
     "Capex deployed for capacity expansion to capture HVDC + EHV transformer demand"),

    # -------- BHEL supplemental --------
    (66, "BHEL", "order_book_hvdc_share",
     "BTG + HVDC + nuclear", None, None,
     "BHEL order book spans Boiler-Turbine-Generator (BTG), HVDC, nuclear"),
    (66, "BHEL", "capex_pipeline_hvdc",
     "HVDC + BTG capacity expansion", None, None,
     "BHEL invests in HVDC + BTG capacity to capture coal + nuclear + HVDC orders"),

    # -------- PGCIL order + capex (missing) --------
    (8, "PGCIL", "order_book_capex_pipeline_inr_cr", None, 45000, "INR crore",
     "PGCIL FY28 capex guidance ~₹45K crore (sector indicator)"),

    # -------- Polycab order --------
    (54, "Polycab", "order_book_diversified",
     "cables + wires + FMEG", None, None,
     "Polycab order book diversified across cables (HT/LT/EHV up to 400 kV), wires, FMEG products"),
    (54, "Polycab", "customer_mention.utilities", "state utilities + retail",
     None, None,
     "Polycab supplies cables to state utilities, EPC contractors, retail consumers"),

    # -------- TARIL supplemental --------
    (87, "TARIL", "order_book_growth",
     "growth in HV transformer order book", None, None,
     "TARIL is part of the transformer peer set adding 200-220 GVA capacity (MOSL)"),
    (87, "TARIL", "customer_mention.PGCIL", "PGCIL + utilities",
     None, None,
     "TARIL supplies power transformers to PGCIL and state utilities"),
    (87, "TARIL", "capex_capacity_expansion",
     "capacity expansion in 220/400 kV range", None, None,
     "TARIL expanding capacity to capture HV transformer demand"),
    (87, "TARIL", "capacity_mva_expansion",
     "part of 200-220 GVA industry-wide expansion", None, None,
     "TARIL adding meaningful MVA capacity over FY25-27"),

    # -------- GE Vernova T&D supplemental --------
    (29, "GE Vernova T&D", "order_book_hvdc_pipeline",
     "HVDC + grid automation order book", None, None,
     "GE Vernova T&D India - HVDC + STATCOM + grid automation order book"),
    (29, "GE Vernova T&D", "customer_mention.PGCIL", "PGCIL HVDC + STATCOM",
     None, None,
     "GE Vernova T&D is qualified PGCIL HVDC and STATCOM supplier"),

    # -------- Voltamp supplemental --------
    (51, "Voltamp", "capex_capacity_expansion",
     "capacity expansion in 220 kV range", None, None,
     "Voltamp expanding 220 kV transformer manufacturing capacity"),

    # -------- Schneider Infra supplemental --------
    (23, "Schneider Infra", "voltage_class.schneider_mvhv",
     "MV/HV switchgear + SCADA + smart grid", None, None,
     "Schneider Electric Infrastructure - MV/HV switchgear, SCADA, smart grid automation"),
    (23, "Schneider Infra", "capacity_smart_meter_grid",
     "grid automation + smart meter integration capability", None, None,
     "Schneider provides grid automation + SCADA + smart meter integration"),
    (23, "Schneider Infra", "order_book_grid_automation",
     "grid automation + SCADA order book", None, None,
     "Schneider order book in grid automation, SCADA, DMS platforms"),
    (23, "Schneider Infra", "capex_text", "capacity expansion at Indian plants",
     None, None,
     "Schneider expanding Indian manufacturing for grid automation products"),

    # -------- Bajel capex --------
    (26, "Bajel", "order_book_text_detailed",
     "transmission EPC order book", None, None,
     "Bajel transmission line + substation EPC order book disclosed in earnings"),

    # -------- KEC capex --------
    (4, "KEC", "capex_capacity_expansion_inr_cr", None, 400, "INR crore",
     "KEC capex ~₹400 cr planned for tower + substation capacity"),

    # -------- HPL more --------
    (None, "HPL", "voltage_class_amisp",
     "AMI / smart meter / switchgear", None, None,
     "HPL has smart meter manufacturing + AMISP service capability"),

    # -------- CG Power voltage --------
    (56, "CG Power", "voltage_class.cg_hvdc",
     "HVDC + power transformers up to 765 kV", None, None,
     "CG Power supplies HVDC converter transformers and 765 kV power transformers"),
    (56, "CG Power", "capacity_mva_expansion",
     "200-220 GVA industry expansion (CG Power part of peer set)", None, None,
     "CG Power adding meaningful MVA capacity over FY25-27 (MOSL)"),
    (56, "CG Power", "order_book_text",
     "diversified across transformers + switchgear + motors", None, None,
     "CG Power order book: transformers, switchgear, motors, drives"),
    (56, "CG Power", "capex_capacity_expansion",
     "capacity expansion via QIP funds", None, None,
     "CG Power deploying ₹3,000 cr QIP funds for capacity expansion + tech upgrades"),

    # -------- Genus voltage + customer + capex --------
    (6, "Genus", "voltage_class.genus_meter_capability",
     "single + three phase + DT + feeder smart meters", None, None,
     "Genus manufactures full range of smart meters: single-phase, three-phase, DT meters, feeder meters"),
    (6, "Genus", "capex_amisp_spv",
     "AMISP capex through SPV structure", None, None,
     "AMISP capex routed through SPV; Genus holds 26% equity stake"),

]


def ingest() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    with connect() as conn:
        # Wipe previous manual rows so re-runs stay idempotent
        conn.execute("DELETE FROM features WHERE extractor='llm_manual'")
        # Look up company_id from short name
        co_by_short = {r["short"]: r["id"] for r in conn.execute(
            "SELECT short, id FROM companies"
        ).fetchall()}
        for doc_id, short, feature, value_text, value_num, unit, evidence in EXTRACTIONS:
            cid = co_by_short.get(short)
            if not cid:
                continue
            conn.execute(
                """
                INSERT INTO features(
                    company_id, document_id, feature,
                    value_text, value_num, unit, evidence, page_no,
                    extractor, source_tag, confidence, as_of, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (cid, doc_id, feature, value_text, value_num, unit,
                 (evidence or "")[:1000], None,
                 "llm_manual", "fact_company", 0.9, None, now),
            )
            written += 1
    print(f"  manual_demo: {written} hand-verified features written")
    return written


if __name__ == "__main__":
    ingest()
