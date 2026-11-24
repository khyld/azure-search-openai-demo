# PRD: Dokumentoversættelse og resumé i Alm. Brand-demoen

**Status:** Produktvalg godkendt; lokal implementering under verifikation, ikke deployeret.  
**Dato:** 27. oktober 2026.  
**Produkt:** Den separate, private Alm. Brand-dokumentdemo.  
**Beslutningsejer:** Demoens produktejer.  
**Grundlag:** [Eksisterende dokumentdemo](document_chat_demo.md).
**Teknisk udmøntning:** [SRD og løsningsarkitektur, udkast](document_translation_srd.md).

**Præcisering, 27. oktober 2026:** Frontend skal bruges i en desktopbrowser på
en pc. Mobiltilpasning er ikke et leverancekrav. Backend og den aftalte
Azure-arkitektur er uændret; dette er ikke et krav om lokal eller offline AI.

## 1. Formål og brugerbehov

En bruger skal kunne uploade et fremmedsproget dokument, stille spørgsmål til det
og bestille en oversættelse eller et resumé fra samme grænseflade. Brugeren skal ikke flytte
dokumentet til et andet værktøj eller miste den eksisterende private session.

Oversættelsen skal være en hel dokumentfil, ikke blot et oversat chatsvar eller
et resumé. Azure Translator Document Translation er den foreslåede tjeneste.
Oversættelse og resumé er uafhængige tilvalg og må ikke ændre originaldokumentet.
Resuméet udarbejdes af en sprogmodel, ikke af Translator, og må ikke kræve,
at brugeren først bestiller en oversættelse.

**Primær brugerhistorie:** Som bruger med et engelsk eller tysk dokument vil jeg
kunne hente en dansk oversættelse, så jeg kan læse og arbejde videre med indholdet.

**Supplerende brugerhistorie:** Som bruger vil jeg kunne oversætte mit dokument
til dansk efter upload uden at skulle uploade det igen.

**Resumébrugerhistorie:** Som bruger vil jeg hurtigt kunne få et generelt overblik
over hele dokumentets formål, hovedpointer og centrale konklusioner og kunne
kontrollere de vigtigste udsagn mod originalen.

## 2. Foreslået MVP

**Fastlagte krav, 26. oktober 2026:** Dansk er eneste målsprog, og løsningen
skal understøtte PDF i første version. En release med kun DOCX-input opfylder ikke kravene.
Oversættelsen leveres som en læsbar dansk DOCX, også ved PDF-input. Der skal
være både checkbox ved upload og en knap efter upload. Første version tilbyder
kun download af oversættelsen, ikke indbygget visning.
For resumé har brugeren valgt et generelt overblik på cirka 400–600 ord,
visning på siden og DOCX-download samt både checkbox ved upload og knap efter upload.
Resuméet skal altid være på dansk og have klikbare kildehenvisninger til
originaldokumentet i brugerfladen. Disse krav er godkendt af brugeren den
26. oktober 2026. DOCX-download indeholder en læsbar kildefortegnelse.
Øvrige valg i dette afsnit er forslag, som kræver produktejerens godkendelse.

| Område | Forslag |
| --- | --- |
| Placering | Samme side som dokumentchatten; ingen ny hjemmeside eller login |
| Startmuligheder | Checkbox **Oversæt til dansk** ved upload samt en knap efter upload; checkboxen er slået fra som standard |
| Målsprog | Udelukkende dansk (`da`); intet målsprogsvalg i brugerfladen |
| Kildesprog | Automatisk registrering; mulighed for manuelt valg af dansk, engelsk eller tysk |
| Filtyper | DOCX samt tekstbaseret og scannet PDF; PDF er obligatorisk ved første release og skal verificeres i den tekniske prøve |
| Resultat | Downloadbar, læsbar dansk DOCX ved både DOCX- og PDF-input; ingen indbygget oversættelsesvisning |
| Antal | Ét aktivt originaldokument og højst én igangværende oversættelse pr. bruger |
| Gentagelse | Oversættelse kan også startes efter upload; ny oversættelse erstatter den tidligere oversættelse |
| Resuméindhold | Generelt overblik: formål, hovedpointer og centrale konklusioner, når de findes i dokumentet |
| Resumélængde | Cirka 400–600 ord; kortere, når dokumentet ikke har indhold til denne længde |
| Resuméstart | Selvstændig checkbox **Lav resumé** ved upload samt knap efter upload; checkboxen er slået fra som standard |
| Resuméresultat | Visning på siden med kildehenvisninger og download af samme resumé som DOCX |
| Chat | Fortsætter med originaldokumentets indeks, historik og kildehenvisninger |
| Session | Samme 120-minutters inaktivitetsregel som dokumentdemoens nuværende specifikation |

Checkboxen udtrykker et ønske om oversættelse. Jobbet starter først efter en
gyldig upload, når serveren har accepteret filen og oversættelsesindstillingerne.
Fravalg før upload medfører ingen Translator-kald. Efter start bruges en særskilt
annulleringskommando, ikke checkboxen, til at stoppe arbejdet.

Resumé kan bestilles alene, oversættelse alene, begge eller ingen af delene.
Fravalg af resumé medfører ingen ekstra modelkald til resumé. Genindlæsning
eller download genbruger et færdigt resultat frem for at generere et nyt.

### Uden for MVP

- Andre målsprog end dansk og flere dokumenter i samme job.
- Indeksering af oversættelsen eller blanding af originale og oversatte chatkilder.
- Tosproget side-om-side-visning, redigering i browseren og godkendelsesworkflow.
- Oversat PDF-output og indbygget visning af oversættelsen.
- Terminologiordbog, Custom Translator og kundespecifikke oversættelsesmodeller.
- Certificeret oversættelse eller garanti for juridisk/forsikringsfaglig korrekthed.
- Specialiserede resumétyper, individuel længdevælger, ekstern research og selvstændig rådgivning.
- DOC, XLSX, PPTX, direkte billedfiler og særskilt oversættelse af billeder i DOCX.
- Automatisk dokumentdeling, e-maillevering og ændringer i den oprindelige RAG-demo.

## 3. Brugerflow og grænseflade

1. Brugeren logger ind som i dag og vælger eller trækker et dokument ind.
2. Brugeren kan aktivere **Oversæt til dansk**. Der er intet målsprogsvalg. Kildesprog står
   på **Automatisk**, med mulighed for manuelt valg ved forkert registrering.
3. Brugeren uploader. Dokumentbehandling og oversættelse får separate statusfelter.
4. Når chatindekset er klar, kan brugeren chatte, selv om oversættelsen stadig arbejder.
5. Når oversættelsen er klar, vises **Hent oversættelse** med downloadikon,
  markeringen **Dansk · DOCX**. Filen downloades uden indbygget visning.
  **Hent original** er fortsat tilgængelig.
6. Ved fejl kan brugeren se en forståelig årsag og vælge **Prøv igen**. Ændring
  af kildesprogsvalg starter ikke automatisk et nyt betalt job.
7. **Nyt dokument**, **Afslut demo** og udløb fjerner adgangen til både original,
   chat og oversættelse og starter oprydning.

En bruger, som fravalgte oversættelse ved upload, kan senere vælge **Oversæt til
dansk** i dokumentområdet. Kildesprogsindstillinger for et igangværende job er
låst; ændring kræver annullering eller afslutning og et nyt job.

### Resuméflow

1. Brugeren kan uafhængigt af oversættelsen aktivere **Lav resumé** ved upload
  eller vælge knappen efter upload uden at uploade filen igen.
2. Resuméet får egen status: **Ikke bestilt**, **I kø**, **Laver resumé**, **Klar**,
  **Mislykkedes**, **Annullerer** eller **Annulleret**. Generering starter først,
  når originaldokumentets nødvendige tekstudtræk er klar.
3. Det færdige resumé vises i et separat område, ikke som et opdigtet chatsvar
  eller en ny brugermeddelelse. Det får overskrift og korte afsnit eller punkter.
4. Klikbare kildehenvisninger åbner originale tekstuddrag, som i dokumentchatten.
  PDF henviser til originale sider; DOCX til nummererede tekstuddrag.
5. **Hent resumé** downloader en DOCX med samme resumétekst og en læsbar
  kildefortegnelse. Filen må ikke være afhængig af aktive sessionslinks for at være forståelig.
6. Fejl kan genforsøges eksplicit; annullering af resumé stopper ikke chatten
  eller en uafhængig oversættelse. Et færdigt resumé genbruges i samme session.

Resuméområdet markeres **AI-genereret resumé. Kontrollér vigtige oplysninger
mod originalen.** Det er resuméet, ikke oversættelsen, der vises på siden.

Statusser: **Ikke bestilt**, **I kø**, **Oversætter**, **Klar**, **Mislykkedes**,
**Annullerer**, **Annulleret**. Et dokument, der er erstattet eller udløbet, må
ikke vise et gammelt resultat. Der vises ikke en opdigtet procent eller sluttid;
delstatus/progress vises kun, hvis tjenesten leverer brugbare data.

Ved downloadområdet vises en kort risikomarkering: **Maskinoversat. Kontrollér
vigtige oplysninger mod originalen.** Fejl og jobstatus skal være tilgængelige
for skærmlæsere, og alle kontroller skal kunne bruges med tastatur.

## 4. Funktionelle krav

| ID | Krav |
| --- | --- |
| F1 | Oversættelse kræver et gyldigt login og servervalideret ejerskab til det aktive dokument. |
| F2 | Backend validerer filtype, størrelse og kildesprogsvalg før et betalt oversættelsesjob. Målsproget er fast `da`; forsøg på andre målsprog afvises server-side. |
| F3 | De eksisterende grænser bevares: 20 MiB, 100 PDF-sider og 200.000 DOCX-teksttegn samt eksisterende ZIP-beskyttelse. Translator kan pålægge yderligere begrænsninger. |
| F4 | Originalfilen bevares uændret. Resultatet er en læsbar dansk DOCX ved både PDF- og DOCX-input og får et entydigt, sikkert filnavn med dansk markering og `.docx`-endelse. |
| F5 | Start, status, annullering og download er knyttet til ejer, dokument-ID og oversættelses-ID; klienten vælger aldrig vilkårlige Storage-adresser. |
| F6 | Status og job-ID gemmes varigt, så genindlæsning, flere faner og procesgenstart ikke starter dubletter. |
| F7 | Gentagne identiske startforsøg genbruger det eksisterende job/resultat frem for at udløse en ny betaling. En bevidst ny oversættelse har et nyt job-ID. |
| F8 | En oversættelsesfejl påvirker ikke en ellers fungerende dokumentchat. En chatindekseringsfejl spærrer ikke et ellers gyldigt oversættelsesresultat. |
| F9 | Resultatet bevarer indhold, afsnit, tabellers sammenhæng og læserækkefølge så vidt muligt. Ved PDF-input prioriteres en læsbar DOCX frem for reproduktion af PDF-layout. Identisk pagination og pixelidentisk layout er ikke et krav. |
| F10 | Krypterede, kopibeskyttede og ikke-understøttede dokumenter afvises med en konkret besked. Blandede sprog og dårlig OCR behandles som kendte kvalitetsrisici. |
| F11 | En eksplicit ny dansk oversættelse, fx efter rettelse af kildesprog, erstatter kun oversættelsen, ikke original eller chathistorik; den gamle oversættelse gøres straks utilgængelig. |
| F12 | Hvis en oversættelse annulleres eller sessionen slutter, må et sent Azure-resultat aldrig blive tilgængeligt igen. |

### Resumékrav

| ID | Krav |
| --- | --- |
| S1 | Resumé understøtter de samme gyldige PDF- og DOCX-input som demoen, herunder maskintrykte scannede PDF'er via OCR. |
| S2 | Resuméet bygger på originaldokumentet, ikke oversættelsen, chatten, eksterne kilder eller kun søgeresultaterne fra en enkelt chatforespørgsel. |
| S3 | Hele det udtrukne dokument indgår i behandlingsgrundlaget. Lange dokumenter må behandles i dele med efterfølgende samlet resumé; der må ikke ske tavs afkortning til de første sider. |
| S4 | Indholdet er et generelt overblik over formål, væsentlige emner og konklusioner, som faktisk findes i dokumentet. Modellen må ikke opfinde konklusioner, frister eller anbefalinger. |
| S5 | Målet er 400–600 ord i selve resuméet, eksklusive kildefortegnelse og advarsel. Korte dokumenter må give kortere resuméer; teksten må ikke fyldes ud med gentagelser eller gæt. |
| S6 | Centrale faktuelle udsagn har klikbare henvisninger i brugerfladen til originalens PDF-sider eller DOCX-tekstafsnit. DOCX-download har en læsbar kildefortegnelse. Henvisninger skal kunne kontrolleres; en citation er ikke i sig selv et faktatjek. |
| S7 | Ved konstateret manglende eller utilstrækkeligt tekstudtræk stoppes resuméfunktionen med en konkret fejl og anmodning om et bedre dokument. Der tilbydes ikke et delvist resumé som alternativ; et tomt dokument giver ikke et opdigtet resumé. |
| S8 | Visning og DOCX-download indeholder samme gemte resuméversion. Genindlæsning, dobbeltklik og download starter ikke nye genereringer for samme anmodning. |
| S9 | Start, status, visning, kilder og download kontrollerer ejer og aktivt dokument. Nyt dokument, afslutning og 120 minutters inaktivitet fjerner adgang; sene resultater kasseres og ryddes op. |
| S10 | Fejl eller annullering i resumé må ikke blokere chat eller oversættelse. Kun en eksplicit ny anmodning/genforsøg kan starte et nyt resuméjob. |
| S11 | Dokumentets tekst og eventuelle indlejrede instruktioner behandles som ukendt input, ikke som systeminstruktioner. Resuméjobbet må ikke hente eksterne URLs eller udføre handlinger angivet i dokumentet. |

## 5. Privatliv, levetid og sletning

Dette afsnit gælder både oversættelse og resumé, inklusive viste resultater,
DOCX-filer, mellemresuméer, kildeuddrag og jobmetadata. Brugere må ikke kunne
genåbne et gammelt resumé efter dokumentudskiftning eller sessionsudløb.

- Adgang kontrolleres ved hver operation, inklusive download, og igen før et
  resultat publiceres. Et job-ID er ikke i sig selv adgangstilladelse.
- Oversættelsesinput, output, midlertidige kopier og jobmetadata følger samme
  session som originalen. Ingen offentlige downloadlinks eller klienteksponerede SAS-tokens.
- Upload, eksplicit oversættelsesstart, download og andre godkendte brugerhandlinger
  kan registreres som aktivitet. Polling og Translator-jobbets arbejde forlænger ikke sessionen.
- Eksplicit resuméstart og åbning af resuméets kilder kan registreres som aktivitet
  under samme regler. Resumégenerering og statuspolling forlænger ikke sessionen.
- Efter 120 minutters inaktivitet afvises adgang straks. Et langt job giver ikke
  automatisk ekstra tid. En udløbet session kan ikke genoplives af et færdigt job.
- Brugerens eksplicitte **Log ud** i dokumentdemoen afslutter også
  dokumentsessionen som **Afslut demo**: adgang til original, chat,
  oversættelse og resumé fjernes, og jobannullering/oprydning igangsættes.
  Dette krav er ikke en påstand om, at appen kan registrere alle eksterne
  Entra-logouts eller browserlukninger.
- Ved ophør sendes annullering, hvis Azure understøtter det. Alle app-kontrollerede
  artefakter slettes, og oprydning gentages efter jobafslutning, så sene writes også fjernes.
- Jobtombstone/oprydningsmetadata uden dokumentindhold må beholdes midlertidigt,
  indtil providerjobbet er afsluttet og sletning er verificeret.
- Fysisk sletning har samme driftsforbehold som dagens demo: udfald kan forsinke
  oprydning. Annullering garanterer ikke, at allerede udført arbejde er uden omkostning.
- Providerens midlertidige behandling og sletning skal gennemgås særskilt;
  appens oprydning er ikke en dokumenteret garanti for sletning i alle Azure-systemer.
- Logs og målinger må ikke indeholde dokumenttekst, fulde filnavne, oversættelser,
  resuméer, tokens eller downloadadresser med legitimationsoplysninger.
- Downloadede filer ligger uden for demoens kontrol og slettes ikke af sessionsudløb.

## 6. Teknisk løsningsretning og afhængigheder

Foreslået løsning er **asynkron Azure Translator Document Translation**, med
ét dokument og fast dansk målsprog (`da`) i hvert job. API-version vælges og fastlåses efter prøven;
Microsoft dokumenterer aktuelt `2026-03-01` som GA. Det er dokumentoversættelse,
ikke Text Translation anvendt på manuelt opsplittet chattekst.

Backend opretter jobbet, gemmer provider-ID, overvåger status og gør resultatet
tilgængeligt gennem en autentificeret downloadroute. Leverandørstatus oversættes
til appens egne statusser. Submission med ukendt udfald afstemmes før retry;
blinde genforsøg må ikke skabe flere betalte jobs.

En ny, dedikeret Translator-resource og private kilde-/målcontainere foreslås.
Den eksisterende dedikerede dokument-Storage kan eventuelt rumme containerne,
men muligheder, rettigheder og retention skal godkendes først. Ingen ændringer
i originaldemoens app, indeks, Storage-retention eller loginregistreringer.

Managed identity og mindst mulige dokumenterede RBAC-rettigheder foretrækkes
både til appens Translator-adgang og Translators Blob-adgang. Serviceidentiteters
containeradgang er bredere end brugeradgang; den skaber ikke brugerisolering i sig selv.
Ingen nye secrets i browseren eller deaktivering af eksisterende sikkerhedskontroller.

Jobovervågning og oprydning må ikke blokere den eksisterende dokumentarbejder
eller fylde den fælles B1-plan med tunge konverteringer. Løsningen skal kunne
genoptage arbejde efter genstart. Eventuelt behov for separat worker, kø eller
større compute er en særskilt beslutning, ikke en del af denne godkendelse.

**Dokumenterede begrænsninger:** Asynkron oversættelse tillader op til 40 MB pr.
dokument; synkron oversættelse op til 10 MB. Derfor passer asynkron behandling
bedst til den eksisterende 20-MiB-grænse. PDF via Document Intelligence er en
batchfunktion i den aktuelle API. En understøttet vej fra PDF-input til dansk
DOCX skal verificeres i prøven, inklusive eventuelt behov for konvertering,
nødvendige DI-ressourcer og ekstra gebyrer. En PDF-fil må ikke blot få en ny
filendelse. Ingen tung lokal konvertering antages godkendt. Den
eksisterende OCR-integration kan ikke uden videre antages at kunne genbruges.

En geografisk europæisk Translator-resource foreslås frem for Global.
Microsoft angiver Frankrig Central eller West Europe som behandlingslokationer
for europæiske resources uden for Schweiz. Sverige-only må ikke loves.
Valgt region, endpoint, Storage-lokation, netværk og datahåndtering kræver
godkendelse før brug med andet end godkendte testdokumenter.

### Teknisk retning for resumé

Genbrug af originalens allerede udtrukne tekst og eksisterende Azure OpenAI-model
foreslås, så oversættelse ikke bliver en afhængighed, og OCR ikke gentages unødigt.
Modellens kontekstgrænse, kvalitet og kapacitet skal verificeres på lange PDF'er;
et eksisterende chatkald med top-k søgeresultater er ikke en dækkende resumépipeline.

Ved opdeling bevares dokumentrækkefølge og kildeankre gennem mellemresuméer og
slutresultat. Dækning registreres, og input/output, retries og samlet tokenforbrug
begrænses. Der kræves ikke et nyt agentframework eller selvstændige værktøjshandlinger.
Konkret model- og promptversion fastlægges i den tekniske prøve og indgår i evalueringen.

Resumé får egen jobstatus og et varigt resultat knyttet til ejer/dokument.
Færdiggørelse kontrollerer fortsat aktivt dokument, så en erstattet eller udløbet
session ikke får et gammelt resultat tilbage. Visning må ikke fortolke dokumenttekst
som HTML eller tillade aktive scripts i modeloutput.

Den eksisterende modeldeployment er GlobalStandard og giver ikke en garanti
for Sverige-only eller EU-only inferens. Translator-regionkrav er derfor ikke
i sig selv en geografisk garanti for resumé eller chat. Dette skal afklares
før brug med fortrolige dokumenter, også hvis selve tekstudtrækket genbruges.

## 7. Drift, ydelse og økonomi

Følgende er foreslåede pilotmål, ikke eksisterende kapacitetsløfter eller SLA'er:

- Jobstart returnerer køstatus inden for 2 sekunder ved varm app under aftalt pilotbelastning.
- 95 % af repræsentative dokumenter på højst 10 sider oversættes inden for 5 minutter,
  målt fra accepteret start til tilgængelig download, inklusive køtid.
- Større dokumenter testes særskilt. Foreslået maksimal jobtid er 30 minutter,
  hvorefter jobbet annulleres og får en tydelig timeoutfejl.
- Startgrænser: ét job pr. bruger, højst to samtidige providerjobs for demoen
  og højst fem nye oversættelser pr. bruger pr. døgn. Værdier er konfigurerbare og skal godkendes.
- Midlertidige providerfejl håndteres med begrænsede retries, backoff og respekt
  for throttling. Permanent afvisning gentages ikke automatisk.
- Mål jobantal, udfald, køtid, behandlingstid, retries, forbrug og oprydningsrestancer
  uden dokumentindhold. Genstart og providersvigt skal kunne diagnosticeres.
- Budget pr. måned fastsættes før aktivering. Forbrug estimeres og måles efter
  valgt API, tekst-/billedbehandling, eventuel OCR, Storage og netværk; ingen fast pris antages her.
- Azure-budgetalarmer er ikke et hårdt forbrugsstop. Backend begrænser nye jobs
  med kvoter og en deaktiveringsfunktion; allerede igangværende arbejde kan stadig koste.

For resumé måles modelkald, input-/outputtokens, køtid, genereringstid,
OCR-dækning og fejl særskilt fra oversættelsen. Tokenbaseret modelpris, eventuel
ekstra OCR og lagring indgår i budgettet. Foreslået pilotgrænse er ét aktivt
resuméjob pr. bruger og højst fem nye genereringer pr. bruger pr. døgn; download
og genbrug af et gemt resumé tæller ikke som en ny generering. Samlet samtidighed
med chat og oversættelse samt resuméets tidsmål godkendes efter kapacitetsmåling.

## 8. Acceptkriterier og releasegate

| ID | Testbar betingelse |
| --- | --- |
| A1 | Uden tilvalg fungerer upload/chat som i dag, uden Translator-kald eller ekstra resumé-modelkald. Resumé alene må ikke starte oversættelse, og oversættelse alene må ikke starte resumé. |
| A2 | Engelsk og tysk DOCX kan oversættes til dansk DOCX. Resultatet kan åbnes i Word og indeholder dansk tekst. Brugerfladen tilbyder ingen andre målsprog. |
| A3 | Engelsk og tysk tekst-PDF og maskintrykt scannet PDF kan oversættes til en læsbar dansk DOCX, der kan åbnes i Word. Første release kræver, at denne PDF-input-gate er bestået; kun DOCX-input er ikke tilstrækkeligt. |
| A4 | Ugyldige filer og overskridelse af backendgrænser afvises før betalt submission. Ikke-understøttede kildesprog og alle andre målsprog end `da` afvises server-side. |
| A5 | En anden bruger får aldrig adgang til status, input eller resultat via manipulerede IDs eller direkte app-downloadforsøg. |
| A6 | To faner, dobbeltklik, netværksretry og procesgenstart giver ikke ekstra submissions for samme startanmodning. |
| A7 | Chat virker under oversættelse og efter oversættelsesfejl; alle chatkilder peger fortsat på originalen. |
| A8 | Nyt dokument, annullering, afslutning, eksplicit Log ud i dokumentdemoen og præcist 120 minutters inaktivitet afviser gamle resultater, også når Azure afleverer sent. App-artefakter ryddes efter providerens afslutning i testmiljøet. |
| A9 | Genindlæsning viser korrekt status/resultat uden nyt job. Polling og baggrundsarbejde flytter ikke inaktivitetsfristen. |
| A10 | Både checkbox ved upload og knap efter upload kan starte dansk oversættelse. UI virker med tastatur, skærmlæser, desktop og mobil uden overlap; download er en gyldig DOCX med korrekt filnavn og adgangskontrol. Indbygget oversættelsesvisning er ikke påkrævet. |
| A11 | Quota, throttling, jobtimeout, genstart og mislykket oprydning testes. Der er ingen dokumenttekst eller secrets i logs. |
| A12 | Pilotens kvalitet, behandlingstid, omkostninger og region/datahåndtering godkendes af produktejer før release. |

### Acceptkriterier for resumé

| ID | Testbar betingelse |
| --- | --- |
| R1 | Både checkbox ved upload og knap efter upload kan starte et resumé alene eller sammen med oversættelse. Ingen ny upload er nødvendig efter første upload. |
| R2 | Engelsk/tysk DOCX, tekst-PDF og scannet PDF giver et generelt overblik over originalen på dansk. Resuméet er altid på dansk uanset originalens sprog; brugerfladen tilbyder ikke andre resumésprog. |
| R3 | Repræsentative dokumenter med nok indhold giver 400–600 ord; korte dokumenter kan give kortere tekst uden opfundet indhold. Ordgrænsen opgøres uden kilder og advarsel. |
| R4 | For lange dokumenter placeres væsentlige oplysninger bevidst i begyndelse, midte og slutning. Dækning kontrolleres i behandlingsgrundlaget og de på forhånd udpegede hovedpointer i slutresuméet. |
| R5 | En reviewer kontrollerer kildehenvisningernes faktiske støtte til centrale udsagn. PDF-sidetal og DOCX-tekstafsnit skal henvise til originalen, uden opdigtede kilder. |
| R6 | Resumé kan læses på siden og hentes som gyldig DOCX med samme indhold og forståelige kildeangivelser. Visning/kilder/download virker med tastatur og på mobil. Oversættelse forbliver download-only. |
| R7 | Tomme eller ulæselige filer og konstateret utilstrækkelig OCR stopper den berørte funktion med besked om at levere et bedre dokument; der publiceres ikke et delvist resultat. Dokumenter med ondsindede instruktioner håndteres som ukendt input, ikke som systeminstruktioner. |
| R8 | Ejerskabsforsøg, to faner, dobbeltklik, genstart, udløb, udskiftning og sent modeloutput testes også for resumé, kilder og DOCX-download. |
| R9 | Resuméfejl/annullering blokerer ikke andre funktioner; modelkvoter og budgetmålinger testes uden dokumentindhold i logs. |

Kvalitetsprøven omfatter mindst 12 godkendte dokumenter fordelt på DOCX,
tekst-PDF og scannet PDF, med engelsk/tysk input, tabeller og lange tekstafsnit.
Alle oversættelsesprøver har dansk som målsprog. Et stort dokument nær hver
uploadgrænse testes desuden for håndtering og ressourceforbrug. Manipulerede
startanmodninger med andre målsprog testes uden betalte providerkald.

En sprogkyndig reviewer vurderer et på forhånd udvalgt sæt afsnit, tabeller og
kritiske oplysninger. Pilotmålet er mindst 95 % acceptable vurderingsenheder
og ingen kritiske betydningsfejl i de kontrollerede beløb, datoer, referencer,
forbehold eller negationer. Kriteriet gælder testmaterialet, ikke en garanti
for ukontrollerede kundedokumenter. Sideantal må ændres; tabeller må ikke få
forkert række-/kolonnetilknytning eller ulæselig/afklippet tekst.

De samme dokumenter kan anvendes til resuméevaluering med et menneskeligt
udarbejdet sæt forventede hovedpointer og kildeankre. Foreslået pilotmål er
mindst 90 % dækning af disse hovedpointer og ingen kritiske, udokumenterede
udsagn eller betydningsændringer i de kontrollerede beløb, datoer og negationer.
Resuméets korthed betyder ikke, at alle detaljer skal gengives; kvalitetsmålet
godkendes før prøven og er ikke en garanti for ukontrollerede dokumenter.

## 9. Leveranceplan og beslutninger

1. **PRD-godkendelse:** Afklar valgene nedenfor og fastlæg budget/testmateriale.
2. **Teknisk prøve:** Verificér API, PDF-input/OCR til dansk DOCX, managed identity, region,
   annullering, sene writes, forbrug og kapacitet med godkendte syntetiske dokumenter.
  Verificér desuden originalbaseret resumé, lange dokumenters dækning,
  kildehenvisninger, modelkapacitet og konsistent visning/DOCX-download.
3. **MVP:** Implementér backendjobs, dansk tilvalg/kildesprogsvalg, PDF/DOCX,
  oversættelsesdownload, selvstændigt resumé med visning/download, sikkerhed og oprydning.
4. **Pilot:** Kør accepttests og kvalitets-/ydelsesmåling. Release kræver
  fungerende PDF-understøttelse, dansk som eneste oversættelsesmålsprog og
  godkendte resumétests; ændring af disse krav kræver ny godkendelse.

### Afklarede rammer for SRD

Følgende er afklaret med brugeren den 26. oktober 2026:

- Første release understøtter dansk, engelsk og tysk som kildesprog. Danske
  dokumenter kan få dansk resumé; unødvendig dansk-til-dansk-oversættelse undgås.
- Første version er alene til godkendte, ikke-følsomme demodokumenter, ikke
  rigtige kundedata eller fortrolige/personfølsomme oplysninger.
- Eksisterende demoopsætning må indgå i løsningsforslaget til disse testdata.
  Der stilles ikke en ny EU-only-garanti; faktiske begrænsninger dokumenteres.
- Chatten fortsætter med originaldokumentet som grundlag, uafhængigt af
  oversættelse og resumé.
- Piloten dimensioneres til 1–5 samtidige brugere. Dette er antal brugere,
  ikke et løfte om fem samtidige providerjobs.
- Fem minutter er ønskemålet også for store dokumenter. Længere ventetid er
  accepteret med tydelig kø-/behandlingsstatus; fem minutter er ikke en
  ubetinget releasebetingelse eller en dokumenteret ydelsesgaranti.
- Budgetrammen er højst 1.000 kr. i ekstra Azure-forbrug pr. måned ud over
  den eksisterende demo. Forventet brug efter idriftsættelse er færre end
  25 dokumenter om måneden samlet for løsningen.
- Et særskilt prisoverslag og en særskilt økonomisk godkendelse kræves ikke,
  når indledende tests underbygger et forventet månedligt merforbrug under
  1.000 DKK. Dette erstatter det tidligere krav om særskilt godkendelse af
  betalte prøver inden for rammen. Indledende tests skal være begrænsede,
  og testforbruget tæller med i samme budgetramme.
- Beregningen baseres konservativt på 25 dokumenter med både oversættelse
  og resumé og medregner dokumentstørrelse, OCR, modelkald, retries, lagring
  og eventuelle faste meromkostninger. Målinger og antagelser dokumenteres;
  fremskrivningen er ikke en garanti for fakturaens maksimum. Hvis rammen
  ikke kan underbygges, afklares ændringer med brugeren før øget forbrug.
- Brugeren leverer godkendte demodokumenter og vurderer resultaternes kvalitet.
- Arkitekturen skal prioritere genbrug af den eksisterende opsætning. Separat
  jobbehandling eller ekstra komponenter indføres kun, hvis målinger viser
  behov, og budgettet kan overholdes. Påvirkning af den eksisterende demo
  skal indgå i målingerne; genbrug er ikke en tilladelse til at forringe den.
- Kvalitet, fuld dokumentbehandling og acceptabel svartid prioriteres over
  at vælge en Free-tier. Betalte servicetiers foretrækkes, hvis gratisversionens
  begrænsninger hindrer kravene. Der må ikke stiltiende behandles færre sider,
  afkortes indhold eller leveres dårligere resultater for at undgå betaling.
  Budgetrammen er fortsat 1.000 DKK i månedligt merforbrug; hvis en egnet
  løsning ikke kan holdes inden for rammen, afklares budget eller scope med
  brugeren frem for at sænke kvaliteten. En betalt tier er ikke i sig selv
  en garanti for højere outputkvalitet.
- Eksplicit **Log ud** i dokumentdemoen afslutter dokumentsessionen og
  starter oprydning på samme måde som **Afslut demo**.
- Hvis konstateret mangelfuldt tekstudtræk/OCR hindrer et dækkende resultat,
  stoppes den berørte oversættelses- eller resuméfunktion med besked om et
  bedre dokument. Delvise resultater tilbydes ikke. Andre funktioner kan
  fortsætte, hvis deres grundlag er tilstrækkeligt.

Automatisk kildesprog med manuel rettelse, jobkvoter, retries, maksimal jobtid,
behandling af dokumenter med blandede sprog og de præcise kvalitetsmål er
fortsat tekniske forslag, som skal konkretiseres og godkendes i SRD'en.
Kriterier for utilstrækkeligt tekstudtræk/OCR fastlægges i den tekniske prøve;
automatisk registrering af alle manglende eller fejllæste oplysninger er
ikke dokumenteret eller garanteret.
De tidligere pilotmål i afsnit 7 er foreløbige; de afklarede rammer ovenfor
er styrende ved udarbejdelsen af SRD'en. Valg af ressourcer, regioner eller
kapacitetsændringer er ikke godkendt til udførelse med denne afklaring.

Godkendelse af PRD er ikke i sig selv godkendelse til provisioning, ændring af
fælles compute eller behandling af fortrolige kundedokumenter.

## 10. Kilder og forbehold

Microsoft-dokumentation konsulteret 26. oktober 2026:

- [Document Translation: aktuel GA og PDF/Office-funktioner](https://learn.microsoft.com/en-us/azure/ai-services/translator/document-translation/latest/overview)
- [Servicegrænser og beskyttede/blandede dokumenter](https://learn.microsoft.com/en-us/azure/ai-services/translator/service-limits#document-translation)
- [Regioner, behandlingslokationer og managed identity](https://learn.microsoft.com/en-us/azure/ai-services/translator/region-support)
- [Managed identity til Blob-adgang](https://learn.microsoft.com/en-us/azure/ai-services/translator/document-translation/how-to-guides/create-use-managed-identities)
- [Translator-priser](https://azure.microsoft.com/pricing/details/cognitive-services/translator/)

Dette er et produktudkast baseret på dokumentation og eksisterende demoarkitektur,
ikke et resultat af en live Translator-prøve. API, formatstøtte, pris og region
skal genbekræftes ved implementering. Oversættelse og resumé er ikke bygget eller deployet.