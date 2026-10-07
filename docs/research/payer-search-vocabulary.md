# Payer Search Vocabulary for a payer Insurer Dropdown

**Purpose**: Reference dataset for fuzzy search, typo tolerance, and alias matching in EHR billing workflows.  
**Data Source**: Public payer registries, CMS, BCBS, state Medicaid agencies, Wikipedia health insurance data.  
**Last Updated**: 2026-10-06  
**Scope**: US-only. Excludes international, closed-book corporate plans, and non-licensed entities.

---

## 1. User Roles & Input Patterns

**Who types**: Front-desk staff (non-clinical, low IT expertise), billing coders, patient registration staff.

**Input patterns**:
- **Abbreviations**: "UHC", "BCBS", "BC", "AARP MA", "HMO", "PPO", "Medicaid", "MassHealth"
- **Partial words**: "Blue", "Cross", "Health", "United", "Aetna", "Plan"
- **State + payer**: "MA Blue Cross", "GA medicaid", "TX UHC", "NY health"
- **Card text**: "BCBS PPO", "UHC Community Plan", "Humana Medicare", "Medicaid Managed Care"
- **Former/alternate names**: "Anthem" (now Elevance), "Health Net" (now Centene), "Coventry" (acquired)
- **Typos**: "Medicad", "Helth", "Anthm", "UHCC", "Cigna" vs "Cignia"
- **Plan variants**: "Empire Blue Cross", "Anthem Blue Cross", "Blue Shield", "Blue Cross Blue Shield"
- **Acronyms**: "CHIP" (Children's Health Insurance Program), "TRICARE", "VA CCN"

---

## 2. Base Payer Names (~150 rows)

```csv
base_name,parent_org,plan_type,states
UnitedHealthcare,UnitedHealth Group,commercial,ALL
Anthem Blue Cross,Elevance Health,commercial,ALL
Cigna Healthcare,Cigna Group,commercial,ALL
Humana,Humana Inc,commercial,ALL
Aetna,CVS Health,commercial,ALL
Kaiser Permanente,Kaiser Foundation,commercial,CA,CO,GA,HI,MD,OR,VA,WA
Geisinger Health Plan,Geisinger,commercial,PA,NJ
HealthPartners,HealthPartners,commercial,MN,WI,CO
Priority Health,Priority Health,commercial,MI,OH
Oscar Health,Oscar Health,commercial,AL,AZ,AR,CO,DE,FL,GA,IL,IN,KS,LA,MD,MI,MN,MS,MO,NE,NY,NV,OH,OK,SC,TN,UT,VA,WI
Regence BlueShield,Regence,commercial,OR,WA,ID,UT
Premera Blue Cross,Premera,commercial,WA,AK,OR,ID,MT
CareSource,CareSource,commercial,OH,IN,KY,WV,MI
Gig Health,Gig Health,commercial,NY,PA,NJ
Molina Healthcare,Molina Healthcare,commercial,AZ,CA,FL,ID,IL,KY,MA,MI,NV,NM,NY,OH,SC,TX,UT,VA,WA,WI,IA,PR
Centene,Centene,commercial,ALL
Affinity Health Plan,Molina Healthcare,commercial,NY
Passport Health Plan,Molina Healthcare,commercial,KY
Blue Cross and Blue Shield of Alabama,BCBS,commercial,AL
Blue Cross and Blue Shield of Arizona,BCBS,commercial,AZ
Arkansas Blue Cross Blue Shield,BCBS,commercial,AR
Blue Cross of California,BCBS,commercial,CA
Blue Shield of California,BCBS,commercial,CA
Rocky Mountain Health Plans,BCBS,commercial,CO
Connecticut Blue Cross Blue Shield,BCBS,commercial,CT
Delaware Blue Cross,BCBS,commercial,DE
Blue Cross and Blue Shield of Florida,BCBS,commercial,FL
Blue Cross and Blue Shield of Georgia,BCBS,commercial,GA
Hawaii Medical Service Association,BCBS,commercial,HI
Blue Cross and Blue Shield of Idaho,BCBS,commercial,ID
Regence BlueShield of Idaho,BCBS,commercial,ID
Blue Cross and Blue Shield of Illinois,BCBS,commercial,IL
Blue Cross and Blue Shield of Indiana,BCBS,commercial,IN
Blue Cross and Blue Shield of Iowa,BCBS,commercial,IA
Blue Cross and Blue Shield of Kansas,BCBS,commercial,KS
Blue Cross and Blue Shield of Kansas City,BCBS,commercial,KS,MO
Blue Cross and Blue Shield of Kentucky,BCBS,commercial,KY
Blue Cross and Blue Shield of Louisiana,BCBS,commercial,LA
Blue Cross and Blue Shield of Maine,BCBS,commercial,ME
CareFirst Blue Cross Blue Shield,BCBS,commercial,MD,VA,DC
Blue Cross Blue Shield of Massachusetts,BCBS,commercial,MA
Blue Cross Blue Shield of Michigan,BCBS,commercial,MI
Blue Cross and Blue Shield of Minnesota,BCBS,commercial,MN
Blue Cross and Blue Shield of Mississippi,BCBS,commercial,MS
Blue Cross and Blue Shield of Missouri,BCBS,commercial,MO
Blue Cross and Blue Shield of Montana,BCBS,commercial,MT
Blue Cross and Blue Shield of Nebraska,BCBS,commercial,NE
Blue Cross and Blue Shield of Nevada,BCBS,commercial,NV
Blue Cross and Blue Shield of New Hampshire,BCBS,commercial,NH
Horizon Blue Cross Blue Shield of New Jersey,BCBS,commercial,NJ
Blue Cross and Blue Shield of New Mexico,BCBS,commercial,NM
Excellus BlueCross BlueShield,BCBS,commercial,NY
Empire BlueChoice Health Insurance,BCBS,commercial,NY
Independence Blue Cross,BCBS,commercial,PA,NJ,DE
Blue Cross and Blue Shield of North Carolina,BCBS,commercial,NC
Blue Cross and Blue Shield of North Dakota,BCBS,commercial,ND
Medical Mutual of Ohio,BCBS,commercial,OH
Blue Cross and Blue Shield of Oklahoma,BCBS,commercial,OK
Regence BlueShield,BCBS,commercial,OR,WA,ID,UT
Capital Blue Cross,BCBS,commercial,PA
Blue Cross and Blue Shield of Pennsylvania,BCBS,commercial,PA
Blue Cross and Blue Shield of Rhode Island,BCBS,commercial,RI
Blue Cross and Blue Shield of South Carolina,BCBS,commercial,SC
Blue Cross and Blue Shield of South Dakota,BCBS,commercial,SD
BlueCross BlueShield of Tennessee,BCBS,commercial,TN
Blue Cross and Blue Shield of Texas,BCBS,commercial,TX
Blue Cross and Blue Shield of Utah,BCBS,commercial,UT
BlueCross BlueShield of Vermont,BCBS,commercial,VT
Anthem Blue Cross and Blue Shield,BCBS,commercial,VA
Premera Blue Cross,BCBS,commercial,WA
Blue Cross and Blue Shield of West Virginia,BCBS,commercial,WV
Blue Cross and Blue Shield of Wisconsin,BCBS,commercial,WI
Blue Cross and Blue Shield of Wyoming,BCBS,commercial,WY
Medi-Cal,State of California,medicaid,CA
MassHealth,State of Massachusetts,medicaid,MA
TennCare,State of Tennessee,medicaid,TN
Soonercare,State of Oklahoma,medicaid,OK
Apple Health,State of Washington,medicaid,WA
MaineCare,State of Maine,medicaid,ME
AHCCCS,State of Arizona,medicaid,AZ
Medicaid,State Health Agency,medicaid,ALL
CHIP,State Health Agency,medicaid,ALL
HealthChoice Illinois,State of Illinois,medicaid,IL
Oregon Health Plan,State of Oregon,medicaid,OR
NJ FamilyCare,State of New Jersey,medicaid,NJ
BadgerCare,State of Wisconsin,medicaid,WI
HUSKY,State of Connecticut,medicaid,CT
Hawk-i,State of Iowa,medicaid,IA
Medicare,U.S. Centers for Medicare and Medicaid Services,medicare,ALL
Medicare Advantage,CMS / Multiple Carriers,medicare_advantage,ALL
AARP Medicare Advantage,UnitedHealth Group,medicare_advantage,ALL
Humana Medicare Advantage,Humana Inc,medicare_advantage,ALL
Cigna Medicare,Cigna Group,medicare_advantage,ALL
Anthem Medicare,Elevance Health,medicare_advantage,ALL
TRICARE,U.S. Department of Defense,military,ALL
VA Community Care Network,U.S. Department of Veterans Affairs,military,ALL
State Compensation Insurance Fund,California,workers_comp,CA
Texas Workers Compensation,State of Texas,workers_comp,TX
National Council on Compensation Insurance,NCCI,workers_comp,ALL
Medical Mutual,National,workers_comp,ALL
Auto Club Insurance,Regional,auto,MI
State Farm Auto,State Farm,auto,ALL
Geico Auto,Berkshire Hathaway,auto,ALL
UMR,Labor Management,tpa,ALL
Meritain Health,Aetna,tpa,ALL
Allied Benefit Systems,Private,tpa,ALL
Ambetter,Centene,marketplace,ALL
WellCare,Centene,medicaid_mco,ALL
Molina Healthcare,Molina Healthcare,medicaid_mco,ALL
CareSource,CareSource,medicaid_mco,OH,IN,KY,WV,MI
Magellan Health,Molina Healthcare,medicaid_mco,AZ,MA,VA
AmeriHealth Caritas,Elevance Health,medicaid_mco,AL,DE,FL,GA,LA,MS,NJ,NM,NY,OH,PA,SC,TX,WI
Amerigroup,Elevance Health,medicaid_mco,CA,CO,CT,DE,FL,GA,KS,LA,MD,MS,MO,NC,OH,OK,OR,PA,SC,TX,VA,WA,WI,DC
Health Net,Centene,medicaid_mco,CA,OR,WA
Fidelis Care,Centene,medicaid_mco,NY
MVP Health Care,MVP Health Care,commercial,NY,PA,VT,NH,CT,MA
Spectrum Health,Spectrum Health,commercial,MI,IN,IL,OH,WI,KY
Community Health Plan,Multiple States,commercial,OR,WA,ID
Sanford Health Plan,Sanford Health,commercial,SD,NE,ND,MN,IA
```

---

## 3. Common Abbreviations & Synonyms (~60 rows)

```csv
abbrev,expansion,note
BC,Blue Cross,part of BCBS plans
BS,Blue Shield,part of BCBS plans
BCBS,Blue Cross Blue Shield,major national federation
BCBC,BlueCross BlueShield,alternate spelling
UHC,UnitedHealthcare,major national carrier
UHG,United Health Group,parent company of UHC
MA,Medicare Advantage,coverage type
MA,Mutual Aid,less common usage
POS,Point of Service,plan type
HMO,Health Maintenance Organization,plan type
PPO,Preferred Provider Organization,plan type
EPO,Exclusive Provider Organization,plan type
MCO,Managed Care Organization,plan administrator
PBM,Pharmacy Benefit Manager,pharmacy services
CHIP,Children's Health Insurance Program,government program
Medicaid,Medicaid,state program (abbreviated)
Medicare,Medicare,federal program (abbreviated)
AARP,American Association of Retired Persons,Medicare brand partner
AARP MA,AARP Medicare Advantage,retiree plans via UHC
EMC,Empire Blue Cross,New York plan
Independence BC,Independence Blue Cross,PA/NJ plan
Capital BC,Capital Blue Cross,Pennsylvania plan
Horizon BCBS,Horizon Blue Cross Blue Shield,New Jersey plan
Regence,Regence BlueShield,Oregon/Washington regional
Premera,Premera Blue Cross,Washington regional
Excellus,Excellus BlueCross BlueShield,New York plan
CareFirst,CareFirst Blue Cross,Maryland/Virginia plan
Wellmark,Wellmark Blue Cross,Iowa/South Dakota plan
HealthPartners,HealthPartners,Minnesota-based regional
Anthem,Elevance Health,rebranded 2024
Elevance,Elevance Health,formerly Anthem
WellCare,Centene subsidiary,Medicare and Medicaid
Ambetter,Centene subsidiary,ACA marketplace
Health Net,Centene subsidiary,Medicaid and commercial
Magellan,Molina subsidiary,behavioral health and Medicaid
Molina,Molina Healthcare,Medicaid MCO
CareSource,CareSource,Ohio-based Medicaid
Centene,Centene,national Medicaid MCO
Aetna,CVS Aetna,commercial and Medicare
Cigna,Cigna Group,commercial and Medicare
Humana,Humana Inc,major insurance carrier
Kaiser,Kaiser Permanente,integrated health system
Geisinger,Geisinger Health Plan,Pennsylvania regional
UMR,Union Management Services,TPA for self-funded plans
Meritain,Meritain Health,TPA acquired by Aetna
Allied Benefit,Allied Benefit Systems,TPA services
Medi-Cal,California Medicaid,state program brand
MassHealth,Massachusetts Medicaid,state program brand
TennCare,Tennessee Medicaid,state program brand
Soonercare,Oklahoma Medicaid,state program brand
Oregon HP,Oregon Health Plan,state program brand
Apple Health,Washington Medicaid,state program brand
TRICARE,Department of Defense,military healthcare
VA CCN,VA Community Care Network,Veterans healthcare
FCM,Federal Employees Program,federal employee coverage
FEP,Federal Employees Program,federal employee coverage
FEHB,Federal Employees Health Benefit Program,federal employee coverage
```

---

## 4. Plan/Product Name Suffixes by Payer Type

**Commercial (HMO/PPO/POS/EPO)**:
- "PPO" (Preferred Provider Organization)
- "HMO" (Health Maintenance Organization)
- "POS" (Point of Service)
- "EPO" (Exclusive Provider Organization)
- "Choice PPO"
- "Choice POS"
- "Choice POS II"
- "Managed Choice"
- "Open Access"
- "Select"
- "Advantage"
- "Flex"
- "Core"
- "Premier"
- "Essential"

**Medicare Advantage**:
- "Medicare Advantage HMO"
- "Medicare Advantage PPO"
- "Medicare Advantage POS"
- "Dual Complete" (for dual-eligible)
- "Special Needs Plan"
- "MAPD" (Medicare Advantage Prescription Drug)
- "Dual Eligible Special Needs Plan"
- "Institutional SNP"
- "Chronic Condition SNP"
- "I-SNP", "C-SNP", "D-SNP"

**Medicaid MCO**:
- "Medicaid Managed Care"
- "Managed Medicaid"
- "Medicaid HMO"
- "Medicaid PPO"
- "Dual Complete"
- "Dual Choice"
- "Long-Term Care Plan"
- "Medicaid Community Plan"

**Medicaid (State Programs)**:
- "[State] Medicaid"
- "[State] Managed Medicaid"
- "Medicaid Fee-for-Service"

**Medicare Supplement (Medigap)**:
- "Medicare Supplement Plan A"
- "Medicare Supplement Plan F"
- "Medigap Plan G"

**CHIP**:
- "CHIP PPO"
- "CHIP Managed Care"

**Marketplace (ACA)**:
- "Silver Plan"
- "Gold Plan"
- "Platinum Plan"
- "Bronze Plan"
- "Catastrophic Plan"

**Military**:
- "TRICARE Prime"
- "TRICARE Select"
- "TRICARE for Life"
- "TRICARE Reserve Select"

---

## 5. Representative Demo Queries & Expected Top Hits

| User Input | Intended Top Hit | Notes |
|---|---|---|
| "BCBS" | Blue Cross Blue Shield of [State] | State-contextualized; varies by patient's state |
| "Blue Cross" | State-specific BCBS plan | Generic; needs state context |
| "UHC" | UnitedHealthcare | National commercial carrier |
| "United Health" | UnitedHealthcare | Partial/typo of UnitedHealthcare |
| "Medicaid" | [State] Medicaid program | Defaults to state Medicaid if known |
| "MassHealth" | MassHealth | Massachusetts-specific state program |
| "Medicare Advantage" | Medicare Advantage (generic) | Enrollment type, not specific carrier |
| "AARP" | AARP Medicare Advantage (UHC) | UHC's Medicare Advantage brand |
| "Anthem Blue" | Anthem Blue Cross (Elevance) | Rebranded to Elevance 2024 |
| "Medicad" (typo) | Medicaid | Typo tolerance: similar edit distance |
| "MA Blue Cross" | Blue Cross Blue Shield of Massachusetts | State prefix + payer name |
| "GA BCBS" | Blue Cross and Blue Shield of Georgia | State + abbreviation |
| "Cigna PPO" | Cigna Healthcare PPO | Plan type suffix |
| "Humana Medicare" | Humana Medicare Advantage | Payer + plan type |
| "Kaiser" | Kaiser Permanente [Region] | Regional integrated system |
| "TennCare" | TennCare | Tennessee Medicaid brand |
| "BC BS" | Blue Cross Blue Shield | Spaces/abbreviation variant |
| "Ambetter" | Ambetter (Centene) | Marketplace plan brand |
| "TRICARE" | TRICARE Prime / TRICARE Select | Military healthcare |
| "State Fund" | State Compensation Insurance Fund (CA) | Workers' compensation; context-dependent |

---

## Data Notes

- **Verification**: BCBS plans, major national carriers (UHC, Anthem, Cigna, Humana, Aetna), Kaiser regions, and state Medicaid programs sourced from public registries.
- **Unverified (general knowledge, not live-verified)**: Specific product naming conventions, smaller regional plans, TPA details, and plan suffix patterns. Recommend live cross-reference with payer websites before production use.
- **Medicaid MCO assignments**: Simplified to major carriers; many states use multiple or rotating MCOs year-to-year.
- **Synonyms**: Include common rebrands (Anthem → Elevance, Health Net → Centene ownership) for backward compatibility.
- **State programs**: ~13 named state Medicaid programs shown; most states use generic "Medicaid" label.

---

## Sources

- **BCBS**: Blue Cross Blue Shield Association public data (BCBS member plans by state)
- **Medicare/Medicaid**: CMS.gov, Medicaid.gov, Medicare.gov public registries
- **State Programs**: State health agency websites (representative examples: Massachusetts MassHealth, Tennessee TennCare, California Medi-Cal, Washington Apple Health, Oklahoma Soonercare)
- **Carriers**: Wikipedia health insurance articles (UnitedHealth Group, Cigna, Humana, Aetna, Kaiser Permanente, Centene, Molina Healthcare)
- **Military**: DoD and VA official healthcare documentation (TRICARE, VA Community Care Network)
- **General industry knowledge**: Common plan type suffixes, abbreviation patterns, payer structure (unverified but industry-standard)

---

## Usage Recommendations

1. **Fuzzy Matching**: Use Levenshtein distance (typos) + abbreviation expansion + state context to rank results.
2. **State Contextualization**: If patient state is known, prioritize state-specific plans in dropdown.
3. **Plan Type Matching**: If provider's network is known (PPO vs. HMO), filter to matching plan types.
4. **Refresh Cadence**: Update payer list and state Medicaid assignments annually; plan suffixes and abbreviations are stable.
5. **Fallback**: If no exact match, show "Other" with free-text entry for customer-specific or regional plans not in dataset.
