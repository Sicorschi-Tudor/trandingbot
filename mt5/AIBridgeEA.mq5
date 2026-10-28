//+------------------------------------------------------------------+
//|                                                   AIBridgeEA.mq5 |
//|  Bridge between MetaTrader 5 and the AI Trading Bot server (v1). |
//|  Sends market/account state via WebRequest and executes ONLY the |
//|  commands returned by the server. It never trades on its own.    |
//+------------------------------------------------------------------+
#property copyright "AI Trading Bot v1"
#property link      "http://127.0.0.1:8787"
#property version   "1.14"
#property description "Punte MT5 <-> server AI Trading Bot (v1)."
#property description "Trimite cotatii, bare, cont, pozitii si deal-uri catre server"
#property description "si executa DOAR comenzile primite de la server (OPEN/MODIFY/CLOSE)."

#include <Trade\Trade.mqh>

#define EA_VERSION        "1.14"
#define MAX_DONE_IDS      500
#define MAX_RESULTS       200
#define MAX_HIST          64
#define HIST_MIN_OK       100
#define HIST_MAX_TRIES    20
#define MAX_DEALS         50
#define HTTP_TIMEOUT_MS   3000
#define CAL_REFRESH_S     600
#define CAL_RETRY_S       60
#define CAL_MAX_ITEMS     200
#define HISTX_MAX_BARS    5000
#define CALHIST_MAX_ITEMS 4000           // CALHIST reply cap (server EA_MAX_ITEMS)
#define AB_ERR_HIST_NOT_READY 4401     // ERR_HISTORY_NOT_FOUND: series still loading

//--- inputs (names fixed by CONTRACT §3)
input string InpServerUrl        = "http://127.0.0.1:8787"; // URL server (fara / la final)
input string InpBridgeKey        = "";                      // MT5_BRIDGE_KEY din .env
input long   InpMagic            = 552211;                  // Magic number al botului (MT5_MAGIC)
input int    InpTimerMs          = 500;                     // Interval sincronizare (ms)
input int    InpDeviationPoints  = 20;                      // Deviatie maxima implicita (puncte)
input int    InpHistoryBars      = 300;                     // Bare trimise la cererea HIST
input bool   InpAllowRealAccount = false;                   // Permite OPEN pe cont REAL (v1: NU)

//--- globals
CTrade   g_trade;
bool     g_busy          = false;
string   g_base_url      = "";
string   g_symbols[];
string   g_tfs[];
string   g_symbols_csv   = "";
string   g_tfs_csv       = "";
string   g_hist_sym[];
string   g_hist_tf[];
int      g_hist_tries[];
int      g_hist_got[];
string   g_done_ids[MAX_DONE_IDS];
int      g_done_count    = 0;
int      g_done_pos      = 0;
string   g_results[];
long     g_offset        = 0;      // server time - UTC, seconds (rounded to 60 s)
datetime g_last_ok_local = 0;
int      g_last_http     = 0;
long     g_sync_ok_count = 0;
string   g_last_msg      = "";
datetime g_last_msg_time = 0;
string   g_last_status   = "";
string   g_instance_id   = "";     // random per OnInit: lets the server detect two EAs syncing at once
//--- economic calendar (sent only on the sync right after a refresh)
datetime g_cal_last      = 0;      // TimeLocal() of the last refresh (0 = refresh on next timer)
string   g_cal_json      = "";
bool     g_cal_ok        = true;
int      g_cal_count     = 0;
bool     g_cal_pending   = false;  // refreshed calendar not yet delivered (HTTP 200)
bool     g_cal_in_req    = false;  // the request being sent carries the calendar
bool     g_cal_fail_logged = false;
//--- archive backfill (HISTX, v1.11): one request at a time, answered in the NEXT sync
bool     g_hx_new        = false;  // HISTX received in the last response, not prepared yet
string   g_hx_sym        = "";
string   g_hx_tf         = "";
long     g_hx_end        = 0;      // UTC (EA clock) - only bars strictly older are sent
int      g_hx_count      = 0;
bool     g_hx_ready      = false;  // g_hx_json prepared, waiting to be delivered (HTTP 200)
bool     g_hx_in_req     = false;  // the request being sent carries the archive
string   g_hx_json       = "";
long     g_hx_served     = 0;
bool     g_hx_maxbars_logged = false;
//--- calendar history (CALHIST, v1.14): one request at a time, answered in the NEXT sync
bool     g_ch_new        = false;  // CALHIST received in the last response, not prepared yet
long     g_ch_from       = 0;      // UTC (EA clock), echoed back
long     g_ch_to         = 0;
int      g_ch_min_imp    = 1;
bool     g_ch_ready      = false;  // g_ch_json prepared, waiting to be delivered (HTTP 200)
bool     g_ch_in_req     = false;  // the request being sent carries the calendar history
string   g_ch_json       = "";
long     g_ch_served     = 0;

//+------------------------------------------------------------------+
//| Small helpers                                                    |
//+------------------------------------------------------------------+
string BoolStr(const bool v)
  {
   return(v ? "true" : "false");
  }

// Number -> JSON string, trailing zeros trimmed.
string FmtD(const double v, const int digits)
  {
   if(!MathIsValidNumber(v))
      return("0");
   int d = digits;
   if(d < 0)
      d = 0;
   if(d > 12)
      d = 12;
   string s = DoubleToString(v, d);
   if(StringFind(s, ".") >= 0)
     {
      int n = StringLen(s);
      while(n > 0 && StringGetCharacter(s, n - 1) == '0')
         n--;
      if(n > 0 && StringGetCharacter(s, n - 1) == '.')
         n--;
      s = StringSubstr(s, 0, n);
     }
   if(s == "" || s == "-" || s == "-0")
      s = "0";
   return(s);
  }

string FmtL(const long v)
  {
   return(IntegerToString(v));
  }

string JsonEscape(const string s)
  {
   string out = "";
   int n = StringLen(s);
   for(int i = 0; i < n; i++)
     {
      ushort c = StringGetCharacter(s, i);
      if(c == '"')
         out += "\\\"";
      else
         if(c == '\\')
            out += "\\\\";
         else
            if(c == '\n')
               out += "\\n";
            else
               if(c == '\r')
                  out += "\\r";
               else
                  if(c == '\t')
                     out += "\\t";
                  else
                     if(c < 0x20)
                        out += StringFormat("\\u%04X", (int)c);
                     else
                        out += ShortToString(c);
     }
   return(out);
  }

string JStr(const string s)
  {
   return("\"" + JsonEscape(s) + "\"");
  }

// Print repeated messages at most every 30 s.
void LogThrottled(const string msg)
  {
   datetime now = TimeLocal();
   if(msg != g_last_msg || (now - g_last_msg_time) >= 30)
     {
      Print(msg);
      g_last_msg      = msg;
      g_last_msg_time = now;
     }
  }

bool InList(const string &arr[], const string v)
  {
   int n = ArraySize(arr);
   for(int i = 0; i < n; i++)
      if(arr[i] == v)
         return(true);
   return(false);
  }

bool TfFromString(const string s, ENUM_TIMEFRAMES &tf)
  {
   if(s == "M1")  { tf = PERIOD_M1;  return(true); }
   if(s == "M5")  { tf = PERIOD_M5;  return(true); }
   if(s == "M15") { tf = PERIOD_M15; return(true); }
   if(s == "M30") { tf = PERIOD_M30; return(true); }
   if(s == "H1")  { tf = PERIOD_H1;  return(true); }
   if(s == "H4")  { tf = PERIOD_H4;  return(true); }
   if(s == "D1")  { tf = PERIOD_D1;  return(true); }
   return(false);
  }

string TfToString(const ENUM_TIMEFRAMES tf)
  {
   switch(tf)
     {
      case PERIOD_M1:
         return("M1");
      case PERIOD_M5:
         return("M5");
      case PERIOD_M15:
         return("M15");
      case PERIOD_M30:
         return("M30");
      case PERIOD_H1:
         return("H1");
      case PERIOD_H4:
         return("H4");
      case PERIOD_D1:
         return("D1");
      default:
         break;
     }
   return("");
  }

//+------------------------------------------------------------------+
//| Time conversion: broker server time -> UTC                       |
//+------------------------------------------------------------------+
void UpdateOffset()
  {
   long raw = (long)TimeTradeServer() - (long)TimeGMT();
   g_offset = (long)MathRound((double)raw / 60.0) * 60;
  }

long ToUtc(const datetime server_time)
  {
   return((long)server_time - g_offset);
  }

long ToUtcMsc(const long server_msc)
  {
   return(server_msc - g_offset * 1000);
  }

//+------------------------------------------------------------------+
//| Symbol helpers                                                   |
//+------------------------------------------------------------------+
int SymDigits(const string sym)
  {
   return((int)SymbolInfoInteger(sym, SYMBOL_DIGITS));
  }

double NormalizePrice(const string sym, const double price)
  {
   if(price <= 0.0)
      return(0.0);
   int    digits = SymDigits(sym);
   double ts     = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double p      = price;
   if(ts > 0.0)
      p = MathRound(p / ts) * ts;
   return(NormalizeDouble(p, digits));
  }

int StepDigits(const double step)
  {
   int    d = 0;
   double s = step;
   while(d < 8 && MathAbs(s - MathRound(s)) > 1e-9)
     {
      s *= 10.0;
      d++;
     }
   return(d);
  }

// Floors volume to the symbol step and clamps to max. Returns 0 if below min.
double NormalizeVolume(const string sym, const double vol)
  {
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      step = (vmin > 0.0 ? vmin : 0.01);
   double v = MathFloor(vol / step + 1e-7) * step;
   if(vmax > 0.0 && v > vmax)
      v = vmax;
   v = NormalizeDouble(v, StepDigits(step));
   if(v < vmin - 1e-9)
      return(0.0);
   return(v);
  }

ENUM_ORDER_TYPE_FILLING PickFilling(const string sym)
  {
   long fm = SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
   if((fm & SYMBOL_FILLING_FOK) != 0)
      return(ORDER_FILLING_FOK);
   if((fm & SYMBOL_FILLING_IOC) != 0)
      return(ORDER_FILLING_IOC);
   return(ORDER_FILLING_RETURN);
  }

bool IsHedging()
  {
   ENUM_ACCOUNT_MARGIN_MODE mm = (ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   return(mm == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING);
  }

bool IsOkRetcode(const uint rc)
  {
   return(rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL);
  }

bool TradingPermitted(string &why)
  {
   if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) == 0)
     {
      why = "Butonul Algo Trading este oprit in terminal";
      return(false);
     }
   if(MQLInfoInteger(MQL_TRADE_ALLOWED) == 0)
     {
      why = "Tranzactionarea algoritmica nu este permisa pentru acest EA (proprietati EA > Common)";
      return(false);
     }
   if(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) == 0)
     {
      why = "Contul nu permite tranzactionare (ACCOUNT_TRADE_ALLOWED=false)";
      return(false);
     }
   if(AccountInfoInteger(ACCOUNT_TRADE_EXPERT) == 0)
     {
      why = "Brokerul nu permite tranzactionare prin EA (ACCOUNT_TRADE_EXPERT=false)";
      return(false);
     }
   why = "";
   return(true);
  }

//+------------------------------------------------------------------+
//| Idempotency: executed cmd_ids (ring buffer, last 500)            |
//+------------------------------------------------------------------+
bool IsDone(const string id)
  {
   for(int i = 0; i < g_done_count; i++)
      if(g_done_ids[i] == id)
         return(true);
   return(false);
  }

void MarkDone(const string id)
  {
   g_done_ids[g_done_pos] = id;
   g_done_pos = (g_done_pos + 1) % MAX_DONE_IDS;
   if(g_done_count < MAX_DONE_IDS)
      g_done_count++;
  }

//+------------------------------------------------------------------+
//| Results buffer (kept until a POST returns HTTP 200)              |
//+------------------------------------------------------------------+
void PushResult(const string json)
  {
   int n = ArraySize(g_results);
   if(n >= MAX_RESULTS)
     {
      Print("ATENTIE: buffer rezultate plin, se elimina cel mai vechi rezultat");
      for(int i = 1; i < n; i++)
         g_results[i - 1] = g_results[i];
      n--;
      ArrayResize(g_results, n);
     }
   ArrayResize(g_results, n + 1);
   g_results[n] = json;
  }

void DropFirstResults(const int k)
  {
   int n = ArraySize(g_results);
   if(k <= 0)
      return;
   if(k >= n)
     {
      ArrayResize(g_results, 0);
      return;
     }
   for(int i = k; i < n; i++)
      g_results[i - k] = g_results[i];
   ArrayResize(g_results, n - k);
  }

string ResultJson(const string cmd_id, const bool ok, const uint retcode, const string desc,
                  const ulong order, const ulong deal, const ulong position,
                  const double price, const double volume, const string comment, const int digits)
  {
   string j = "{";
   j += "\"cmd_id\":" + JStr(cmd_id);
   j += ",\"ok\":" + BoolStr(ok);
   j += ",\"retcode\":" + FmtL((long)retcode);
   j += ",\"retcode_desc\":" + JStr(desc);
   j += ",\"order\":" + FmtL((long)order);
   j += ",\"deal\":" + FmtL((long)deal);
   j += ",\"position\":" + FmtL((long)position);
   j += ",\"price\":" + FmtD(price, digits);
   j += ",\"volume\":" + FmtD(volume, 8);
   j += ",\"comment\":" + JStr(comment);
   j += "}";
   return(j);
  }

void PushFail(const string cmd_id, const uint retcode, const string comment)
  {
   Print("Comanda ", cmd_id, " ESUATA: ", comment, " (retcode ", retcode, ")");
   PushResult(ResultJson(cmd_id, false, retcode, comment, 0, 0, 0, 0.0, 0.0, comment, 5));
  }

//+------------------------------------------------------------------+
//| HIST requests                                                    |
//+------------------------------------------------------------------+
int FindHist(const string sym, const string tf)
  {
   int n = ArraySize(g_hist_sym);
   for(int i = 0; i < n; i++)
      if(g_hist_sym[i] == sym && g_hist_tf[i] == tf)
         return(i);
   return(-1);
  }

void AddHist(const string sym, const string tf)
  {
   ENUM_TIMEFRAMES t = PERIOD_CURRENT;
   if(sym == "" || !TfFromString(tf, t))
      return;
   if(FindHist(sym, tf) >= 0)
      return;
   int n = ArraySize(g_hist_sym);
   if(n >= MAX_HIST)
      return;
   SymbolSelect(sym, true);
   ArrayResize(g_hist_sym, n + 1);
   ArrayResize(g_hist_tf, n + 1);
   ArrayResize(g_hist_tries, n + 1);
   ArrayResize(g_hist_got, n + 1);
   g_hist_sym[n]   = sym;
   g_hist_tf[n]    = tf;
   g_hist_tries[n] = 0;
   g_hist_got[n]   = 0;
  }

void RemoveHistAt(const int idx)
  {
   int n = ArraySize(g_hist_sym);
   if(idx < 0 || idx >= n)
      return;
   for(int i = idx + 1; i < n; i++)
     {
      g_hist_sym[i - 1]   = g_hist_sym[i];
      g_hist_tf[i - 1]    = g_hist_tf[i];
      g_hist_tries[i - 1] = g_hist_tries[i];
      g_hist_got[i - 1]   = g_hist_got[i];
     }
   ArrayResize(g_hist_sym, n - 1);
   ArrayResize(g_hist_tf, n - 1);
   ArrayResize(g_hist_tries, n - 1);
   ArrayResize(g_hist_got, n - 1);
  }

//+------------------------------------------------------------------+
//| Economic calendar (MQL5 built-in, needs a connected terminal)    |
//+------------------------------------------------------------------+
int CalImportance(const ENUM_CALENDAR_EVENT_IMPORTANCE imp)
  {
   if(imp == CALENDAR_IMPORTANCE_HIGH)
      return(3);
   if(imp == CALENDAR_IMPORTANCE_MODERATE)
      return(2);
   if(imp == CALENDAR_IMPORTANCE_LOW)
      return(1);
   return(0);
  }

void CalendarFailed(const string why)
  {
   g_cal_json    = "";
   g_cal_ok      = false;
   g_cal_count   = 0;
   g_cal_pending = true;
   g_cal_last    = TimeLocal() - CAL_REFRESH_S + CAL_RETRY_S;   // retry sooner
   if(!g_cal_fail_logged)
     {
      Print("ATENTIE: calendarul economic MT5 nu este disponibil (", why, "). Terminalul trebuie sa fie conectat la broker.");
      g_cal_fail_logged = true;
     }
  }

// Events with importance >= MODERATE from 1 h ago to +24 h (server time), converted to UTC.
void RefreshCalendar()
  {
   g_cal_last = TimeLocal();
   UpdateOffset();
   datetime now_srv = TimeTradeServer();
   datetime dt_from = now_srv - 3600;
   datetime dt_to   = now_srv + 86400;
   MqlCalendarValue values[];
   ResetLastError();
   bool got = CalendarValueHistory(values, dt_from, dt_to);
   int  err = GetLastError();
   int  n   = ArraySize(values);
   if(!got && err != 0)
     {
      CalendarFailed("CalendarValueHistory eroare " + IntegerToString(err));
      return;
     }
   string out   = "";
   int    added = 0;
   int    bad   = 0;
   for(int i = 0; i < n && added < CAL_MAX_ITEMS; i++)
     {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev))
        {
         bad++;
         continue;
        }
      int imp = CalImportance(ev.importance);
      if(imp < 2)
         continue;
      // all-day / no-time entries carry 00:00 as time: useless for a news blackout
      if(ev.time_mode == CALENDAR_TIMEMODE_DATE || ev.time_mode == CALENDAR_TIMEMODE_NOTIME)
         continue;
      MqlCalendarCountry c;
      if(!CalendarCountryById(ev.country_id, c))
        {
         bad++;
         continue;
        }
      if(c.currency == "")
         continue;
      string j = "{";
      j += "\"t\":" + FmtL(ToUtc(values[i].time));
      j += ",\"ccy\":" + JStr(c.currency);
      j += ",\"imp\":" + IntegerToString(imp);
      j += ",\"name\":" + JStr(ev.name);
      j += "}";
      if(added > 0)
         out += ",";
      out += j;
      added++;
     }
   if(n > 0 && bad >= n)
     {
      CalendarFailed("CalendarEventById/CalendarCountryById au esuat, eroare " + IntegerToString(GetLastError()));
      return;
     }
   g_cal_json    = out;
   g_cal_ok      = true;
   g_cal_count   = added;
   g_cal_pending = true;
   if(g_cal_fail_logged)
     {
      Print("Calendarul economic MT5 este din nou disponibil (", added, " evenimente)");
      g_cal_fail_logged = false;
     }
  }

void MaybeRefreshCalendar()
  {
   if(g_cal_last == 0 || (TimeLocal() - g_cal_last) >= CAL_REFRESH_S)
      RefreshCalendar();
  }

//+------------------------------------------------------------------+
//| Archive backfill (HISTX|sym|tf|end_utc|count)                    |
//+------------------------------------------------------------------+
void SetHistx(const string sym, const string tf, const string end_s, const string count_s)
  {
   ENUM_TIMEFRAMES t = PERIOD_CURRENT;
   long end_utc = StringToInteger(end_s);
   int  cnt     = (int)StringToInteger(count_s);
   if(sym == "" || !TfFromString(tf, t) || end_utc <= 0 || cnt <= 0)
     {
      Print("Linie HISTX invalida ignorata: ", sym, "|", tf, "|", end_s, "|", count_s);
      return;
     }
   if(cnt > HISTX_MAX_BARS)
      cnt = HISTX_MAX_BARS;   // 5000 bars x ~100 bytes keeps the sync body well under 1.5 MB
   g_hx_sym   = sym;
   g_hx_tf    = tf;
   g_hx_end   = end_utc;
   g_hx_count = cnt;
   g_hx_new   = true;
  }

string ArchiveReplyErr(const string bars, const string err_json)
  {
   string j = "{\"symbol\":" + JStr(g_hx_sym);
   j += ",\"tf\":" + JStr(g_hx_tf);
   j += ",\"end_utc\":" + FmtL(g_hx_end);
   j += ",\"srv_off\":" + FmtL(g_offset);    // offset used for every bar "t" of this batch
   j += ",\"bars\":[" + bars + "]";
   if(err_json != "")
      j += ",\"err\":" + err_json;
   j += "}";
   return(j);
  }

string ArchiveReply(const string bars, const int err)
  {
   return(ArchiveReplyErr(bars, (err != 0 ? IntegerToString(err) : "")));
  }

// Runs after the response was handled (commands already executed), so a slow history
// download never delays quotes, positions or command results of the current sync.
void PrepareArchive()
  {
   g_hx_new   = false;
   g_hx_ready = true;
   ENUM_TIMEFRAMES tf = PERIOD_CURRENT;
   if(!TfFromString(g_hx_tf, tf))
     {
      g_hx_json = ArchiveReply("", 4003);
      return;
     }
   if(!SymbolSelect(g_hx_sym, true))
     {
      //--- v1.13: name not known at this broker -> the server tries the next alias / marks it unavailable
      g_hx_json = ArchiveReplyErr("", JStr("UNKNOWN_SYMBOL"));
      return;
     }
   UpdateOffset();
   long     end_srv  = g_hx_end + g_offset;
   datetime from_srv = (datetime)(end_srv - 1);
   long     psec     = (long)PeriodSeconds(tf);
   MqlRates rates[];
   ArraySetAsSeries(rates, false);   // index 0 = oldest
   //--- history already in the terminal: range copy over local data only (no server download, never waits);
   //--- only when nothing local is older than end_utc does CopyRates start (and wait for) a download
   long term_first = SeriesInfoInteger(g_hx_sym, tf, SERIES_TERMINAL_FIRSTDATE);
   int  n   = 0;
   int  err = 0;
   if(term_first > 0 && term_first < (long)from_srv)
     {
      long span = (long)g_hx_count * psec * 2 + 4 * 86400;   // weekends/holidays: a partial batch is fine
      long start_srv = (long)from_srv - span;
      if(start_srv < term_first)
         start_srv = term_first;
      ResetLastError();
      n   = CopyRates(g_hx_sym, tf, (datetime)start_srv, from_srv, rates);
      err = GetLastError();
      if(n > g_hx_count)
        {
         ArrayRemove(rates, 0, (uint)(n - g_hx_count));   // keep the newest g_hx_count bars
         n = g_hx_count;
        }
     }
   if(n <= 0)
     {
      ResetLastError();
      n   = CopyRates(g_hx_sym, tf, from_srv, g_hx_count, rates);
      err = GetLastError();
     }
   if(n < 0)
     {
      g_hx_json = ArchiveReply("", (err != 0 ? err : AB_ERR_HIST_NOT_READY));
      return;
     }
   if(n == 0)
     {
      //--- nothing older at the broker -> empty list (server marks the pair done); still loading -> err
      long first_srv = SeriesInfoInteger(g_hx_sym, tf, SERIES_SERVER_FIRSTDATE);
      long synced    = SeriesInfoInteger(g_hx_sym, tf, SERIES_SYNCHRONIZED);
      long maxbars   = (long)TerminalInfoInteger(TERMINAL_MAXBARS);
      long local_bars = (long)Bars(g_hx_sym, tf);
      if(maxbars > 0 && local_bars >= maxbars)
        {
         if(!g_hx_maxbars_logged)
           {
            Print("Arhiva: limita 'Max bars in chart' (", maxbars, ") atinsa pentru ", g_hx_sym, " ", g_hx_tf,
                  " - mareste-o in Tools > Options > Charts pentru mai mult istoric");
            g_hx_maxbars_logged = true;
           }
         g_hx_json = ArchiveReply("", 0);
         return;
        }
      if(synced == 0 || first_srv <= 0 || first_srv < (long)from_srv - PeriodSeconds(tf))
         g_hx_json = ArchiveReply("", AB_ERR_HIST_NOT_READY);
      else
         g_hx_json = ArchiveReply("", 0);
      return;
     }
   int    digits  = SymDigits(g_hx_sym);
   long   now_srv = (long)TimeTradeServer();
   string out     = "";
   string chunk   = "";
   int    added   = 0;
   for(int i = 0; i < n; i++)
     {
      long t_srv = (long)rates[i].time;
      if(t_srv >= end_srv || t_srv + psec > now_srv)
         continue;   // only CLOSED bars strictly older than end_utc
      string b = "{\"t\":" + FmtL(t_srv - g_offset);
      b += ",\"o\":" + FmtD(rates[i].open, digits);
      b += ",\"h\":" + FmtD(rates[i].high, digits);
      b += ",\"l\":" + FmtD(rates[i].low, digits);
      b += ",\"c\":" + FmtD(rates[i].close, digits);
      b += ",\"tv\":" + FmtL(rates[i].tick_volume);
      b += ",\"sp\":" + FmtL((long)rates[i].spread);
      b += "}";
      if(added > 0)
         chunk += ",";
      chunk += b;
      added++;
      if(StringLen(chunk) > 16000)
        {
         out += chunk;   // append in blocks: far fewer reallocations of the big string
         chunk = "";
        }
     }
   out += chunk;
   g_hx_json = ArchiveReply(out, 0);
   g_hx_served++;
  }

//+------------------------------------------------------------------+
//| Calendar history (CALHIST|from_utc|to_utc|min_imp), v1.14        |
//+------------------------------------------------------------------+
void SetCalHist(const string from_s, const string to_s, const string imp_s)
  {
   long f = StringToInteger(from_s);
   long t = StringToInteger(to_s);
   int  m = (imp_s == "" ? 1 : (int)StringToInteger(imp_s));
   if(f <= 0 || t <= f)
     {
      Print("Linie CALHIST invalida ignorata: ", from_s, "|", to_s, "|", imp_s);
      return;
     }
   g_ch_from    = f;
   g_ch_to      = t;
   g_ch_min_imp = (m < 0 ? 0 : (m > 3 ? 3 : m));
   g_ch_new     = true;
  }

// Raw MqlCalendarValue long (value x 10^6); LONG_MIN = not set -> null
string CalRaw(const long v)
  {
   if(v == LONG_MIN)
      return("null");
   return(FmtL(v));
  }

string CalHistReply(const string items, const bool ok, const int err, const int total, const bool truncated)
  {
   string j = "{\"from_utc\":" + FmtL(g_ch_from);
   j += ",\"to_utc\":" + FmtL(g_ch_to);
   j += ",\"srv_off\":" + FmtL(g_offset);
   j += ",\"ok\":" + BoolStr(ok);
   j += ",\"err\":" + IntegerToString(err);
   j += ",\"total\":" + IntegerToString(total);
   j += ",\"truncated\":" + BoolStr(truncated);
   j += ",\"items\":[" + items + "]}";
   return(j);
  }

// Runs after the response was handled (commands already executed), like PrepareArchive(), so it never
// delays quotes, positions or command results. CalendarValueHistory reads the terminal's local calendar
// database (no broker download, no waiting); the reply goes out with the NEXT sync.
void PrepareCalHist()
  {
   g_ch_new   = false;
   g_ch_ready = true;
   UpdateOffset();
   datetime from_srv = (datetime)(g_ch_from + g_offset);
   datetime to_srv   = (datetime)(g_ch_to + g_offset);
   MqlCalendarValue values[];
   ResetLastError();
   bool got = CalendarValueHistory(values, from_srv, to_srv);   // no country/currency filter
   int  err = GetLastError();
   if(!got && err != 0)
     {
      g_ch_json = CalHistReply("", false, err, 0, false);
      return;
     }
   int n = ArraySize(values);
   //--- values come back in time order: keep the first CALHIST_MAX_ITEMS matches, count all of them
   string out    = "";
   string chunk  = "";
   int    added  = 0;
   int    total  = 0;
   ulong  last_eid = 0;
   bool   have_ev  = false;
   bool   ev_ok    = false;
   ulong  last_cid = 0;
   bool   have_c   = false;
   bool   c_ok     = false;
   MqlCalendarEvent   ev;
   MqlCalendarCountry c;
   for(int i = 0; i < n; i++)
     {
      if(!have_ev || values[i].event_id != last_eid)
        {
         ev_ok    = CalendarEventById(values[i].event_id, ev);
         last_eid = values[i].event_id;
         have_ev  = true;
         //--- country 0 (Worldwide) is a valid id: track "loaded" separately from the id itself
         if(ev_ok && (!have_c || ev.country_id != last_cid))
           {
            c_ok     = CalendarCountryById(ev.country_id, c);
            last_cid = ev.country_id;
            have_c   = true;
           }
        }
      if(!ev_ok || !c_ok)
         continue;
      int imp = CalImportance(ev.importance);
      if(imp < g_ch_min_imp)
         continue;
      total++;
      if(added >= CALHIST_MAX_ITEMS)
         continue;
      string j = "{\"id\":" + FmtL((long)values[i].id);
      j += ",\"eid\":" + FmtL((long)values[i].event_id);
      j += ",\"t\":" + FmtL(ToUtc(values[i].time));
      j += ",\"ccy\":" + JStr(c.currency);
      j += ",\"cc\":" + JStr(c.code);
      j += ",\"imp\":" + IntegerToString(imp);
      j += ",\"name\":" + JStr(ev.name);
      j += ",\"tm\":" + IntegerToString((int)ev.time_mode);
      j += ",\"unit\":" + IntegerToString((int)ev.unit);
      j += ",\"mult\":" + IntegerToString((int)ev.multiplier);
      j += ",\"dig\":" + IntegerToString((int)ev.digits);
      j += ",\"a\":" + CalRaw(values[i].actual_value);
      j += ",\"f\":" + CalRaw(values[i].forecast_value);
      j += ",\"p\":" + CalRaw(values[i].prev_value);
      j += ",\"r\":" + CalRaw(values[i].revised_prev_value);
      j += ",\"imx\":" + IntegerToString((int)values[i].impact_type);
      j += ",\"rev\":" + IntegerToString((int)values[i].revision);
      j += ",\"per\":" + FmtL((long)values[i].period);
      j += "}";
      if(added > 0)
         chunk += ",";
      chunk += j;
      added++;
      if(StringLen(chunk) > 16000)
        {
         out += chunk;   // append in blocks: far fewer reallocations of the big string
         chunk = "";
        }
     }
   out += chunk;
   g_ch_json = CalHistReply(out, true, 0, total, total > added);
   g_ch_served++;
  }

//+------------------------------------------------------------------+
//| JSON builders                                                    |
//+------------------------------------------------------------------+
string TerminalJson()
  {
   bool connected = (TerminalInfoInteger(TERMINAL_CONNECTED) != 0);
   bool trade_ok  = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0) && (MQLInfoInteger(MQL_TRADE_ALLOWED) != 0);
   string j = "{";
   j += "\"connected\":" + BoolStr(connected);
   j += ",\"trade_allowed\":" + BoolStr(trade_ok);
   j += ",\"build\":" + FmtL((long)TerminalInfoInteger(TERMINAL_BUILD));
   j += "}";
   return(j);
  }

string AccountJson()
  {
   string j = "{";
   j += "\"login\":" + FmtL(AccountInfoInteger(ACCOUNT_LOGIN));
   j += ",\"server\":" + JStr(AccountInfoString(ACCOUNT_SERVER));
   j += ",\"company\":" + JStr(AccountInfoString(ACCOUNT_COMPANY));
   j += ",\"trade_mode\":" + FmtL(AccountInfoInteger(ACCOUNT_TRADE_MODE));
   j += ",\"currency\":" + JStr(AccountInfoString(ACCOUNT_CURRENCY));
   j += ",\"balance\":" + FmtD(AccountInfoDouble(ACCOUNT_BALANCE), 2);
   j += ",\"equity\":" + FmtD(AccountInfoDouble(ACCOUNT_EQUITY), 2);
   j += ",\"margin\":" + FmtD(AccountInfoDouble(ACCOUNT_MARGIN), 2);
   j += ",\"margin_free\":" + FmtD(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2);
   j += ",\"leverage\":" + FmtL(AccountInfoInteger(ACCOUNT_LEVERAGE));
   j += ",\"profit\":" + FmtD(AccountInfoDouble(ACCOUNT_PROFIT), 2);
   j += ",\"trade_allowed\":" + BoolStr(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) != 0);
   j += ",\"trade_expert\":" + BoolStr(AccountInfoInteger(ACCOUNT_TRADE_EXPERT) != 0);
   j += ",\"margin_mode\":" + FmtL(AccountInfoInteger(ACCOUNT_MARGIN_MODE));
   j += "}";
   return(j);
  }

// Returns "" if the symbol has no valid quote yet.
string SymbolJson(const string sym)
  {
   MqlTick tick;
   ZeroMemory(tick);
   if(!SymbolInfoTick(sym, tick))
      return("");
   if(tick.bid <= 0.0 || tick.ask <= 0.0)
      return("");
   int    digits = SymDigits(sym);
   double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
   long   spread = SymbolInfoInteger(sym, SYMBOL_SPREAD);
   if(point > 0.0)
      spread = (long)MathRound((tick.ask - tick.bid) / point);

   string j = "{";
   j += "\"symbol\":" + JStr(sym);
   j += ",\"bid\":" + FmtD(tick.bid, digits);
   j += ",\"ask\":" + FmtD(tick.ask, digits);
   j += ",\"time_msc\":" + FmtL(ToUtcMsc((long)tick.time_msc));
   j += ",\"spread_points\":" + FmtL(spread);
   j += ",\"point\":" + FmtD(point, 10);
   j += ",\"digits\":" + FmtL((long)digits);
   j += ",\"volume_min\":" + FmtD(SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN), 8);
   j += ",\"volume_max\":" + FmtD(SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX), 8);
   j += ",\"volume_step\":" + FmtD(SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP), 8);
   j += ",\"stops_level\":" + FmtL(SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL));
   j += ",\"freeze_level\":" + FmtL(SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL));
   j += ",\"tick_value\":" + FmtD(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE), 10);
   j += ",\"tick_size\":" + FmtD(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE), 10);
   j += ",\"contract_size\":" + FmtD(SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE), 4);
   j += ",\"trade_mode\":" + FmtL(SymbolInfoInteger(sym, SYMBOL_TRADE_MODE));
   j += ",\"filling_mode\":" + FmtL(SymbolInfoInteger(sym, SYMBOL_FILLING_MODE));
   j += "}";
   return(j);
  }

// Comma-separated bar objects, oldest -> newest. Last one may be forming.
string BarsJson(const string sym, const string tfname, const int count, int &copied)
  {
   copied = 0;
   ENUM_TIMEFRAMES tf = PERIOD_CURRENT;
   if(!TfFromString(tfname, tf) || count <= 0)
      return("");
   MqlRates rates[];
   ArraySetAsSeries(rates, false);   // index 0 = oldest
   int n = CopyRates(sym, tf, 0, count, rates);
   if(n <= 0)
      return("");
   copied = n;
   int    digits = SymDigits(sym);
   string esym   = JStr(sym);
   string out    = "";
   for(int i = 0; i < n; i++)
     {
      if(i > 0)
         out += ",";
      out += "{\"symbol\":" + esym;
      out += ",\"tf\":\"" + tfname + "\"";
      out += ",\"t\":" + FmtL(ToUtc(rates[i].time));
      out += ",\"o\":" + FmtD(rates[i].open, digits);
      out += ",\"h\":" + FmtD(rates[i].high, digits);
      out += ",\"l\":" + FmtD(rates[i].low, digits);
      out += ",\"c\":" + FmtD(rates[i].close, digits);
      out += ",\"tv\":" + FmtL(rates[i].tick_volume);
      out += ",\"sp\":" + FmtL((long)rates[i].spread);
      out += "}";
     }
   return(out);
  }

string PositionsJson()
  {
   string out   = "";
   int    total = PositionsTotal();
   int    added = 0;
   for(int i = 0; i < total; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      string sym    = PositionGetString(POSITION_SYMBOL);
      int    digits = SymDigits(sym);
      string j = "{";
      j += "\"ticket\":" + FmtL((long)ticket);
      j += ",\"symbol\":" + JStr(sym);
      j += ",\"type\":" + FmtL(PositionGetInteger(POSITION_TYPE));
      j += ",\"volume\":" + FmtD(PositionGetDouble(POSITION_VOLUME), 8);
      j += ",\"price_open\":" + FmtD(PositionGetDouble(POSITION_PRICE_OPEN), digits);
      j += ",\"sl\":" + FmtD(PositionGetDouble(POSITION_SL), digits);
      j += ",\"tp\":" + FmtD(PositionGetDouble(POSITION_TP), digits);
      j += ",\"price_current\":" + FmtD(PositionGetDouble(POSITION_PRICE_CURRENT), digits);
      j += ",\"profit\":" + FmtD(PositionGetDouble(POSITION_PROFIT), 2);
      j += ",\"swap\":" + FmtD(PositionGetDouble(POSITION_SWAP), 2);
      j += ",\"magic\":" + FmtL(PositionGetInteger(POSITION_MAGIC));
      j += ",\"comment\":" + JStr(PositionGetString(POSITION_COMMENT));
      j += ",\"time\":" + FmtL(ToUtc((datetime)PositionGetInteger(POSITION_TIME)));
      j += "}";
      if(added > 0)
         out += ",";
      out += j;
      added++;
     }
   return(out);
  }

string DealsJson()
  {
   datetime now = TimeCurrent();
   if(!HistorySelect(now - 86400, now + 60))
      return("");
   string out   = "";
   int    added = 0;
   int    total = HistoryDealsTotal();
   //--- pass 1: positions opened by this EA (IN deals with our magic)
   long bot_pos[];
   int  nbot = 0;
   for(int i = 0; i < total; i++)
     {
      ulong tk = HistoryDealGetTicket(i);
      if(tk == 0)
         continue;
      if(HistoryDealGetInteger(tk, DEAL_MAGIC) == InpMagic && (ENUM_DEAL_ENTRY)HistoryDealGetInteger(tk, DEAL_ENTRY) == DEAL_ENTRY_IN)
        {
         ArrayResize(bot_pos, nbot + 1, 64);
         bot_pos[nbot] = HistoryDealGetInteger(tk, DEAL_POSITION_ID);
         nbot++;
        }
     }
   //--- pass 2: our deals + exits of our positions made by others (manual close, stop-out: magic 0)
   for(int i = total - 1; i >= 0 && added < MAX_DEALS; i--)
     {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;
      long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      if(magic != InpMagic)
        {
         if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY) == DEAL_ENTRY_IN)
            continue;
         long pid = HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
         bool ours = false;
         for(int k = 0; k < nbot; k++)
            if(bot_pos[k] == pid)
              {
               ours = true;
               break;
              }
         if(!ours)
            continue;
        }
      string sym    = HistoryDealGetString(ticket, DEAL_SYMBOL);
      int    digits = (sym != "" ? SymDigits(sym) : 5);
      string j = "{";
      j += "\"ticket\":" + FmtL((long)ticket);
      j += ",\"order\":" + FmtL(HistoryDealGetInteger(ticket, DEAL_ORDER));
      j += ",\"position_id\":" + FmtL(HistoryDealGetInteger(ticket, DEAL_POSITION_ID));
      j += ",\"symbol\":" + JStr(sym);
      j += ",\"type\":" + FmtL(HistoryDealGetInteger(ticket, DEAL_TYPE));
      j += ",\"entry\":" + FmtL(HistoryDealGetInteger(ticket, DEAL_ENTRY));
      j += ",\"volume\":" + FmtD(HistoryDealGetDouble(ticket, DEAL_VOLUME), 8);
      j += ",\"price\":" + FmtD(HistoryDealGetDouble(ticket, DEAL_PRICE), digits);
      j += ",\"profit\":" + FmtD(HistoryDealGetDouble(ticket, DEAL_PROFIT), 2);
      j += ",\"commission\":" + FmtD(HistoryDealGetDouble(ticket, DEAL_COMMISSION), 2);
      j += ",\"swap\":" + FmtD(HistoryDealGetDouble(ticket, DEAL_SWAP), 2);
      j += ",\"magic\":" + FmtL(magic);
      j += ",\"reason\":" + FmtL(HistoryDealGetInteger(ticket, DEAL_REASON));
      j += ",\"time\":" + FmtL(ToUtc((datetime)HistoryDealGetInteger(ticket, DEAL_TIME)));
      j += ",\"comment\":" + JStr(HistoryDealGetString(ticket, DEAL_COMMENT));
      j += "}";
      if(added > 0)
         out += ",";
      out += j;
      added++;
     }
   return(out);
  }

// Builds the full sync request. n_results = how many buffered results were included.
string BuildRequest(int &n_results)
  {
   string j = "{";
   j += "\"ea_version\":\"" + EA_VERSION + "\"";
   j += ",\"magic\":" + FmtL(InpMagic);
   j += ",\"instance_id\":" + JStr(g_instance_id);
   j += ",\"time_utc\":" + FmtL((long)TimeGMT());
   j += ",\"srv_offset\":" + FmtL(g_offset);   // v1.12: lets the server undo broker DST changes in old bars
   j += ",\"terminal\":" + TerminalJson();
   j += ",\"account\":" + AccountJson();

   //--- symbols
   string sj    = "";
   int    nsym  = ArraySize(g_symbols);
   int    added = 0;
   for(int i = 0; i < nsym; i++)
     {
      string one = SymbolJson(g_symbols[i]);
      if(one == "")
         continue;
      if(added > 0)
         sj += ",";
      sj += one;
      added++;
     }
   j += ",\"symbols\":[" + sj + "]";

   //--- bars: full history for pending HIST pairs, last 3 bars for every symbol x tf
   string bj   = "";
   int    nh   = ArraySize(g_hist_sym);
   int    hcnt = (InpHistoryBars > 3 ? InpHistoryBars : 3);
   for(int h = 0; h < nh; h++)
     {
      int got = 0;
      string part = BarsJson(g_hist_sym[h], g_hist_tf[h], hcnt, got);
      g_hist_got[h] = got;
      if(part == "")
         continue;
      if(bj != "")
         bj += ",";
      bj += part;
     }
   int ntf = ArraySize(g_tfs);
   for(int s = 0; s < nsym; s++)
     {
      for(int t = 0; t < ntf; t++)
        {
         if(FindHist(g_symbols[s], g_tfs[t]) >= 0)
            continue;
         int got2 = 0;
         string part2 = BarsJson(g_symbols[s], g_tfs[t], 3, got2);
         if(part2 == "")
            continue;
         if(bj != "")
            bj += ",";
         bj += part2;
        }
     }
   j += ",\"bars\":[" + bj + "]";

   j += ",\"positions\":[" + PositionsJson() + "]";
   j += ",\"deals\":[" + DealsJson() + "]";

   //--- buffered command results
   n_results = ArraySize(g_results);
   string rj = "";
   for(int r = 0; r < n_results; r++)
     {
      if(r > 0)
         rj += ",";
      rj += g_results[r];
     }
   j += ",\"results\":[" + rj + "]";

   //--- economic calendar: only after a refresh, until delivered
   g_cal_in_req = g_cal_pending;
   if(g_cal_pending)
     {
      j += ",\"calendar\":[" + g_cal_json + "]";
      j += ",\"calendar_ok\":" + BoolStr(g_cal_ok);
     }

   //--- archive batch requested by the previous response (HISTX), until delivered
   g_hx_in_req = g_hx_ready;
   if(g_hx_ready)
      j += ",\"archive\":[" + g_hx_json + "]";

   //--- calendar history requested by the previous response (CALHIST, v1.14), until delivered
   g_ch_in_req = g_ch_ready;
   if(g_ch_ready)
      j += ",\"calendar_hist\":" + g_ch_json;
   j += "}";
   return(j);
  }

//+------------------------------------------------------------------+
//| Command execution                                                |
//+------------------------------------------------------------------+
ulong FindOpenedPosition(const string sym, const string cmt, const ulong deal, const ulong order)
  {
   if(deal > 0 && HistoryDealSelect(deal))
     {
      long pid = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      if(pid > 0)
         return((ulong)pid);
     }
   ulong best      = 0;
   long  best_time = 0;
   int   total     = PositionsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != sym)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      if(cmt != "" && PositionGetString(POSITION_COMMENT) != cmt)
         continue;
      long pt = PositionGetInteger(POSITION_TIME_MSC);
      if(pt >= best_time)
        {
         best_time = pt;
         best      = t;
        }
     }
   if(best > 0)
      return(best);
   return(order);
  }

void ExecOpen(const string &p[], const int k)
  {
   string id = p[1];
   if(IsDone(id))
     {
      Print("Comanda ", id, " a fost deja executata - ignorata (idempotenta)");
      return;
     }
   MarkDone(id);

   string sym  = p[2];
   string side = p[3];
   double vol  = StringToDouble(p[4]);
   double sl   = StringToDouble(p[5]);
   double tp   = StringToDouble(p[6]);
   long   dev  = StringToInteger(p[7]);
   string cmt  = (k >= 9 ? p[8] : "");
   //--- v1.10 fields (absent from older servers: checks disabled)
   double ref_price = (k >= 12 ? StringToDouble(p[9]) : 0.0);
   long   max_slip  = (k >= 12 ? StringToInteger(p[10]) : 0);
   long   expires   = (k >= 12 ? StringToInteger(p[11]) : 0);

   if(expires > 0 && (long)TimeGMT() > expires)
     {
      string ec = "Comanda expirata (" + FmtL((long)TimeGMT() - expires) + " s dupa termen) - nu se trimite";
      Print("Comanda ", id, " ESUATA: ", ec);
      PushResult(ResultJson(id, false, TRADE_RETCODE_TIMEOUT, "TIMEOUT (expired)", 0, 0, 0, 0.0, 0.0, ec, 5));
      return;
     }

   if(side != "BUY" && side != "SELL")
     {
      PushFail(id, 0, "Directie invalida: " + side);
      return;
     }
   if(!InpAllowRealAccount && AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO)
     {
      PushFail(id, 0, "EA refuza OPEN: contul nu este DEMO (v1)");
      return;
     }
   string why = "";
   if(!TradingPermitted(why))
     {
      PushFail(id, TRADE_RETCODE_CLIENT_DISABLES_AT, why);
      return;
     }
   if(!SymbolSelect(sym, true))
     {
      PushFail(id, 0, "Simbol indisponibil la broker: " + sym);
      return;
     }
   if(sl <= 0.0 || tp <= 0.0)
     {
      PushFail(id, TRADE_RETCODE_INVALID_STOPS, "SL si TP sunt obligatorii");
      return;
     }
   MqlTick tick;
   ZeroMemory(tick);
   if(!SymbolInfoTick(sym, tick) || tick.bid <= 0.0 || tick.ask <= 0.0)
     {
      PushFail(id, TRADE_RETCODE_PRICE_OFF, "Nu exista cotatie pentru " + sym);
      return;
     }

   int    digits = SymDigits(sym);
   ENUM_ORDER_TYPE otype = (side == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   double price  = NormalizeDouble(otype == ORDER_TYPE_BUY ? tick.ask : tick.bid, digits);
   double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(ref_price > 0.0 && max_slip > 0 && point > 0.0)
     {
      double cur   = (otype == ORDER_TYPE_BUY ? tick.ask : tick.bid);
      double drift = MathAbs(cur - ref_price);
      if(drift > (double)max_slip * point + point * 0.01)
        {
         string dc = "Pretul s-a mutat " + FmtD(drift / point, 1) + " puncte fata de referinta " + FmtD(ref_price, digits) +
                     " (max " + FmtL(max_slip) + ") - nu se trimite";
         Print("Comanda ", id, " ESUATA: ", dc);
         PushResult(ResultJson(id, false, TRADE_RETCODE_PRICE_CHANGED, "PRICE_CHANGED (ref drift)", 0, 0, 0, cur, vol, dc, digits));
         return;
        }
     }
   double nsl    = NormalizePrice(sym, sl);
   double ntp    = NormalizePrice(sym, tp);
   double nvol   = NormalizeVolume(sym, vol);
   if(nvol <= 0.0)
     {
      PushFail(id, TRADE_RETCODE_INVALID_VOLUME, "Volum invalid/sub minim: " + FmtD(vol, 8));
      return;
     }
   ulong deviation = (ulong)(dev > 0 ? dev : InpDeviationPoints);
   ENUM_ORDER_TYPE_FILLING filling = PickFilling(sym);

   //--- pre-check
   MqlTradeRequest req;
   MqlTradeCheckResult chk;
   ZeroMemory(req);
   ZeroMemory(chk);
   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = sym;
   req.volume       = nvol;
   req.type         = otype;
   req.price        = price;
   req.sl           = nsl;
   req.tp           = ntp;
   req.deviation    = deviation;
   req.magic        = (ulong)InpMagic;
   req.comment      = cmt;
   req.type_filling = filling;
   req.type_time    = ORDER_TIME_GTC;
   if(!OrderCheck(req, chk))
     {
      string c = "OrderCheck: " + chk.comment;
      Print("Comanda ", id, " respinsa la OrderCheck: ", chk.comment, " (retcode ", chk.retcode, ")");
      PushResult(ResultJson(id, false, chk.retcode, chk.comment, 0, 0, 0, price, nvol, c, digits));
      return;
     }

   //--- send
   g_trade.SetExpertMagicNumber((ulong)InpMagic);
   g_trade.SetDeviationInPoints(deviation);
   g_trade.SetTypeFilling(filling);
   ResetLastError();
   bool sent = g_trade.PositionOpen(sym, otype, nvol, price, nsl, ntp, cmt);
   uint   rc    = g_trade.ResultRetcode();
   string desc  = g_trade.ResultRetcodeDescription();
   ulong  order = g_trade.ResultOrder();
   ulong  deal  = g_trade.ResultDeal();
   double fprice = g_trade.ResultPrice();
   double fvol   = g_trade.ResultVolume();
   string rcmt   = g_trade.ResultComment();
   if(!sent && rc == 0)
      rcmt = "PositionOpen a esuat, eroare " + IntegerToString(GetLastError());
   bool ok = IsOkRetcode(rc);
   ulong position = 0;
   if(ok)
      position = FindOpenedPosition(sym, cmt, deal, order);
   Print("OPEN ", side, " ", sym, " vol=", FmtD(nvol, 8), " sl=", FmtD(nsl, digits), " tp=", FmtD(ntp, digits),
         " -> ", (ok ? "OK" : "ESUAT"), " retcode=", rc, " (", desc, ") pozitie=", position, " cmd=", id);
   PushResult(ResultJson(id, ok, rc, desc, order, deal, position, fprice, fvol, rcmt, digits));
  }

void ExecModify(const string &p[], const int k)
  {
   string id = p[1];
   if(IsDone(id))
     {
      Print("Comanda ", id, " a fost deja executata - ignorata (idempotenta)");
      return;
     }
   MarkDone(id);

   ulong  ticket = (ulong)StringToInteger(p[2]);
   double sl     = StringToDouble(p[3]);
   double tp     = StringToDouble(p[4]);

   if(!PositionSelectByTicket(ticket))
     {
      PushFail(id, TRADE_RETCODE_POSITION_CLOSED, "Pozitia " + FmtL((long)ticket) + " nu exista");
      return;
     }
   if(PositionGetInteger(POSITION_MAGIC) != InpMagic)
     {
      PushFail(id, TRADE_RETCODE_REJECT, "Pozitia " + FmtL((long)ticket) + " nu apartine botului (magic diferit)");
      return;
     }
   string why = "";
   if(!TradingPermitted(why))
     {
      PushFail(id, TRADE_RETCODE_CLIENT_DISABLES_AT, why);
      return;
     }
   string sym    = PositionGetString(POSITION_SYMBOL);
   double pvol   = PositionGetDouble(POSITION_VOLUME);
   int    digits = SymDigits(sym);
   double nsl    = NormalizePrice(sym, sl);
   double ntp    = NormalizePrice(sym, tp);

   g_trade.SetExpertMagicNumber((ulong)InpMagic);
   ResetLastError();
   bool sent = g_trade.PositionModify(ticket, nsl, ntp);
   uint   rc   = g_trade.ResultRetcode();
   string desc = g_trade.ResultRetcodeDescription();
   string rcmt = g_trade.ResultComment();
   if(!sent && rc == 0)
      rcmt = "PositionModify a esuat, eroare " + IntegerToString(GetLastError());
   bool ok = IsOkRetcode(rc);
   Print("MODIFY #", ticket, " sl=", FmtD(nsl, digits), " tp=", FmtD(ntp, digits),
         " -> ", (ok ? "OK" : "ESUAT"), " retcode=", rc, " (", desc, ") cmd=", id);
   PushResult(ResultJson(id, ok, rc, desc, g_trade.ResultOrder(), g_trade.ResultDeal(), ticket, 0.0, pvol, rcmt, digits));
  }

void ExecClose(const string &p[], const int k)
  {
   string id = p[1];
   if(IsDone(id))
     {
      Print("Comanda ", id, " a fost deja executata - ignorata (idempotenta)");
      return;
     }
   MarkDone(id);

   ulong  ticket = (ulong)StringToInteger(p[2]);
   double vol    = StringToDouble(p[3]);

   if(!PositionSelectByTicket(ticket))
     {
      PushFail(id, TRADE_RETCODE_POSITION_CLOSED, "Pozitia " + FmtL((long)ticket) + " nu exista (posibil deja inchisa)");
      return;
     }
   if(PositionGetInteger(POSITION_MAGIC) != InpMagic)
     {
      PushFail(id, TRADE_RETCODE_REJECT, "Pozitia " + FmtL((long)ticket) + " nu apartine botului (magic diferit)");
      return;
     }
   string why = "";
   if(!TradingPermitted(why))
     {
      PushFail(id, TRADE_RETCODE_CLIENT_DISABLES_AT, why);
      return;
     }
   string sym    = PositionGetString(POSITION_SYMBOL);
   double pvol   = PositionGetDouble(POSITION_VOLUME);
   int    digits = SymDigits(sym);
   double step   = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      step = 0.01;
   double nvol   = (vol > 0.0 ? NormalizeVolume(sym, vol) : 0.0);
   bool   full   = (vol <= 0.0 || nvol <= 0.0 || nvol >= pvol - step / 2.0);
   if(vol > 0.0 && nvol <= 0.0)
     {
      PushFail(id, TRADE_RETCODE_INVALID_VOLUME, "Volum partial invalid/sub minim: " + FmtD(vol, 8));
      return;
     }

   g_trade.SetExpertMagicNumber((ulong)InpMagic);
   g_trade.SetDeviationInPoints((ulong)InpDeviationPoints);
   g_trade.SetTypeFilling(PickFilling(sym));
   ResetLastError();
   bool sent = false;
   if(full)
      sent = g_trade.PositionClose(ticket);
   else
     {
      if(!IsHedging())
        {
         PushFail(id, TRADE_RETCODE_REJECT, "Inchiderea partiala este suportata doar pe conturi hedging in v1");
         return;
        }
      sent = g_trade.PositionClosePartial(ticket, nvol);
     }
   uint   rc   = g_trade.ResultRetcode();
   string desc = g_trade.ResultRetcodeDescription();
   string rcmt = g_trade.ResultComment();
   if(!sent && rc == 0)
      rcmt = "Inchiderea a esuat, eroare " + IntegerToString(GetLastError());
   bool ok = IsOkRetcode(rc);
   Print("CLOSE #", ticket, (full ? " complet" : " partial ") , (full ? "" : FmtD(nvol, 8)),
         " -> ", (ok ? "OK" : "ESUAT"), " retcode=", rc, " (", desc, ") cmd=", id);
   PushResult(ResultJson(id, ok, rc, desc, g_trade.ResultOrder(), g_trade.ResultDeal(), ticket,
                         g_trade.ResultPrice(), g_trade.ResultVolume(), rcmt, digits));
  }

//+------------------------------------------------------------------+
//| Response parsing                                                 |
//+------------------------------------------------------------------+
void SetSymbols(const string csv)
  {
   if(csv == g_symbols_csv)
      return;
   string parts[];
   int n = StringSplit(csv, ',', parts);
   string fresh[];
   int m = 0;
   for(int i = 0; i < n; i++)
     {
      string s = parts[i];
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s == "" || InList(fresh, s))
         continue;
      if(!SymbolSelect(s, true))
        {
         Print("ATENTIE: simbolul '", s, "' nu exista la acest broker (verifica numele, ex. EURUSD.a / EURUSDm)");
         continue;
        }
      ArrayResize(fresh, m + 1);
      fresh[m] = s;
      m++;
     }
   g_symbols_csv = csv;
   if(m == 0)
     {
      Print("ATENTIE: niciun simbol valid primit de la server, se foloseste ", _Symbol);
      ArrayResize(g_symbols, 1);
      g_symbols[0] = _Symbol;
      return;
     }
   ArrayResize(g_symbols, m);
   for(int j = 0; j < m; j++)
      g_symbols[j] = fresh[j];
   Print("Simboluri active: ", csv);
  }

void SetTimeframes(const string csv)
  {
   if(csv == g_tfs_csv)
      return;
   string parts[];
   int n = StringSplit(csv, ',', parts);
   string fresh[];
   int m = 0;
   for(int i = 0; i < n; i++)
     {
      string s = parts[i];
      StringTrimLeft(s);
      StringTrimRight(s);
      ENUM_TIMEFRAMES tf = PERIOD_CURRENT;
      if(!TfFromString(s, tf) || InList(fresh, s))
         continue;
      ArrayResize(fresh, m + 1);
      fresh[m] = s;
      m++;
     }
   g_tfs_csv = csv;
   if(m == 0)
      return;
   ArrayResize(g_tfs, m);
   for(int j = 0; j < m; j++)
      g_tfs[j] = fresh[j];
   Print("Timeframe-uri active: ", csv);
  }

void HandleResponse(const string body_in)
  {
   string body = body_in;
   StringReplace(body, "\r", "");
   string lines[];
   int n = StringSplit(body, '\n', lines);
   for(int i = 0; i < n; i++)
     {
      string line = lines[i];
      StringTrimLeft(line);
      StringTrimRight(line);
      if(line == "")
         continue;
      string p[];
      int k = StringSplit(line, '|', p);
      if(k <= 0)
         continue;
      string cmd = p[0];
      if(cmd == "OK")
         continue;
      if(cmd == "SYMBOLS")
        {
         if(k >= 2)
            SetSymbols(p[1]);
         continue;
        }
      if(cmd == "TF")
        {
         if(k >= 2)
            SetTimeframes(p[1]);
         continue;
        }
      if(cmd == "CALENDAR")
        {
         g_cal_last = 0;   // server asks for a fresh calendar (e.g. after its restart)
         continue;
        }
      if(cmd == "HISTX")
        {
         if(k >= 5)
            SetHistx(p[1], p[2], p[3], p[4]);
         continue;
        }
      if(cmd == "CALHIST")
        {
         if(k >= 3)
            SetCalHist(p[1], p[2], (k >= 4 ? p[3] : "1"));
         continue;
        }
      if(cmd == "HIST")
        {
         if(k >= 3)
            AddHist(p[1], p[2]);
         continue;
        }
      if(cmd == "OPEN")
        {
         if(k >= 8)
            ExecOpen(p, k);
         else
            if(k >= 2 && !IsDone(p[1]))
              {
               MarkDone(p[1]);
               PushFail(p[1], 0, "Linie OPEN malformata");
              }
         continue;
        }
      if(cmd == "MODIFY")
        {
         if(k >= 5)
            ExecModify(p, k);
         else
            if(k >= 2 && !IsDone(p[1]))
              {
               MarkDone(p[1]);
               PushFail(p[1], 0, "Linie MODIFY malformata");
              }
         continue;
        }
      if(cmd == "CLOSE")
        {
         if(k >= 4)
            ExecClose(p, k);
         else
            if(k >= 2 && !IsDone(p[1]))
              {
               MarkDone(p[1]);
               PushFail(p[1], 0, "Linie CLOSE malformata");
              }
         continue;
        }
      Print("Instructiune necunoscuta de la server ignorata: ", line);
     }
  }

//+------------------------------------------------------------------+
//| Sync cycle                                                       |
//+------------------------------------------------------------------+
void UpdateChartStatus()
  {
   string conn;
   long age = (g_last_ok_local > 0 ? (long)(TimeLocal() - g_last_ok_local) : -1);
   if(g_last_ok_local > 0 && age <= 5)
      conn = "CONECTAT";
   else
      conn = "DECONECTAT";
   string s = "AIBridgeEA v" + EA_VERSION + "  |  " + conn;
   s += "\nServer: " + g_base_url + "   HTTP: " + IntegerToString(g_last_http);
   s += "\nUltimul sync reusit: " + (age >= 0 ? IntegerToString(age) + " s in urma" : "niciodata");
   s += "\nSimboluri: " + (g_symbols_csv != "" ? g_symbols_csv : _Symbol) + "   TF: " + (g_tfs_csv != "" ? g_tfs_csv : "M1,M5,H1");
   s += "\nRezultate in asteptare: " + IntegerToString(ArraySize(g_results)) + "   Magic: " + FmtL(InpMagic);
   s += "\nCalendar economic: " + (g_cal_ok ? IntegerToString(g_cal_count) + " evenimente (24 h)" : "indisponibil");
   s += "\nArhiva istoric: " + FmtL(g_hx_served) + " loturi trimise";
   s += "\nIstoric calendar: " + FmtL(g_ch_served) + " ferestre trimise";
   s += "\nEA-ul executa DOAR comenzile serverului.";
   if(s != g_last_status)
     {
      Comment(s);
      g_last_status = s;
     }
  }

void DoSync()
  {
   UpdateOffset();
   int n_hist = ArraySize(g_hist_sym);
   int n_res  = 0;
   string body = BuildRequest(n_res);

   char data[];
   char result[];
   string result_headers = "";
   int len = StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(len > 0)
      ArrayResize(data, len - 1);   // drop trailing null

   string url     = g_base_url + "/bridge/v1/sync";
   string headers = "Content-Type: application/json\r\nX-Bridge-Key: " + InpBridgeKey + "\r\n";

   ResetLastError();
   int code = WebRequest("POST", url, headers, HTTP_TIMEOUT_MS, data, result, result_headers);
   g_last_http = code;

   if(code == -1)
     {
      int err = GetLastError();
      if(err == 4014)
         LogThrottled("WebRequest interzis (eroare 4014). Add URL to Tools > Options > Expert Advisors > Allow WebRequest: " + g_base_url);
      else
         LogThrottled("WebRequest a esuat (eroare " + IntegerToString(err) + "). Ruleaza serverul la " + g_base_url + " ? Verifica si lista Allow WebRequest.");
      return;
     }

   string resp = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

   if(code == 401)
     {
      LogThrottled("Bad bridge key (HTTP 401): InpBridgeKey trebuie sa fie egal cu MT5_BRIDGE_KEY din .env");
      return;
     }
   if(code == 403)
     {
      LogThrottled("HTTP 403: serverul refuza conexiunea. Seteaza MT5_BRIDGE_KEY in .env (nu poate fi gol) si reporneste serverul.");
      return;
     }
   if(code != 200)
     {
      LogThrottled("Raspuns neasteptat de la server: HTTP " + IntegerToString(code) + " " + StringSubstr(resp, 0, 200));
      return;
     }

   //--- success: buffered results delivered, update HIST bookkeeping
   if(g_last_ok_local == 0)
      Print("Conectat la server ", g_base_url, " (cont ", AccountInfoInteger(ACCOUNT_LOGIN), ", server ", AccountInfoString(ACCOUNT_SERVER), ")");
   g_last_ok_local = TimeLocal();
   g_sync_ok_count++;
   DropFirstResults(n_res);
   if(g_cal_in_req)
      g_cal_pending = false;
   if(g_hx_in_req)
     {
      g_hx_ready = false;
      g_hx_json  = "";
     }
   if(g_ch_in_req)
     {
      g_ch_ready = false;
      g_ch_json  = "";
     }

   int hmin = (InpHistoryBars < HIST_MIN_OK ? InpHistoryBars : HIST_MIN_OK);
   if(hmin < 1)
      hmin = 1;
   int hn = ArraySize(g_hist_sym);
   if(n_hist > hn)
      n_hist = hn;
   for(int h = n_hist - 1; h >= 0; h--)
     {
      g_hist_tries[h]++;
      if(g_hist_got[h] >= hmin || g_hist_tries[h] >= HIST_MAX_TRIES)
         RemoveHistAt(h);
     }

   HandleResponse(resp);
   if(g_hx_new)
      PrepareArchive();
   if(g_ch_new)
      PrepareCalHist();
  }

//+------------------------------------------------------------------+
//| Event handlers                                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_base_url = InpServerUrl;
   StringTrimLeft(g_base_url);
   StringTrimRight(g_base_url);
   while(StringLen(g_base_url) > 0 && StringGetCharacter(g_base_url, StringLen(g_base_url) - 1) == '/')
      g_base_url = StringSubstr(g_base_url, 0, StringLen(g_base_url) - 1);

   if(InpBridgeKey == "")
      Print("ATENTIE: InpBridgeKey este gol. Copiaza valoarea MT5_BRIDGE_KEY din .env in parametrii EA-ului.");
   if(MQLInfoInteger(MQL_TESTER) != 0)
      Print("ATENTIE: WebRequest nu functioneaza in Strategy Tester. Ataseaza EA-ul pe un grafic real.");

   ArrayResize(g_symbols, 1);
   g_symbols[0] = _Symbol;
   ArrayResize(g_tfs, 3);
   g_tfs[0] = "M1";
   g_tfs[1] = "M5";
   g_tfs[2] = "H1";
   ArrayResize(g_results, 0);
   ArrayResize(g_hist_sym, 0);
   ArrayResize(g_hist_tf, 0);
   ArrayResize(g_hist_tries, 0);
   ArrayResize(g_hist_got, 0);

   MathSrand((int)(GetTickCount() ^ (uint)(ChartID() & 0x7FFFFFFF)));
   g_instance_id = IntegerToString((long)(GetTickCount64() % 1000000000)) + "-" + IntegerToString(MathRand()) +
                   "-" + IntegerToString(ChartID() % 100000);
   g_cal_last        = 0;
   g_cal_pending     = false;
   g_cal_fail_logged = false;
   g_hx_new          = false;
   g_hx_ready        = false;
   g_hx_in_req       = false;
   g_hx_json         = "";
   g_ch_new          = false;
   g_ch_ready        = false;
   g_ch_in_req       = false;
   g_ch_json         = "";
   RefreshCalendar();

   g_trade.SetExpertMagicNumber((ulong)InpMagic);
   g_trade.SetDeviationInPoints((ulong)InpDeviationPoints);
   g_trade.SetAsyncMode(false);
   g_trade.LogLevel(LOG_LEVEL_ERRORS);

   int ms = InpTimerMs;
   if(ms < 100)
      ms = 100;
   if(!EventSetMillisecondTimer(ms))
     {
      Print("Nu se poate porni timer-ul (eroare ", GetLastError(), ")");
      return(INIT_FAILED);
     }
   Print("AIBridgeEA v", EA_VERSION, " pornit (instanta ", g_instance_id, "). Server: ", g_base_url, " | magic ", InpMagic,
         " | cont ", AccountInfoInteger(ACCOUNT_LOGIN),
         (AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO ? " (DEMO)" : " (NU este DEMO!)"));
   UpdateChartStatus();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Comment("");
   if(ArraySize(g_results) > 0)
      Print("ATENTIE: ", ArraySize(g_results), " rezultate nelivrate la server la oprirea EA-ului");
   Print("AIBridgeEA oprit (motiv ", reason, ")");
  }

void OnTimer()
  {
   if(g_busy)
      return;
   g_busy = true;
   MaybeRefreshCalendar();
   DoSync();
   UpdateChartStatus();
   g_busy = false;
  }

void OnTick()
  {
   // Intentionally empty: all work happens in OnTimer.
  }
//+------------------------------------------------------------------+
