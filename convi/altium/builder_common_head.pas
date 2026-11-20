{ ==========================================================================
  Convi PCB builder (DelphiScript, runs inside Altium Designer)

  Reads a Convi IR file (see convi/altium/ir.py) and creates PcbDoc / PcbLib
  documents through the Altium API.  Placeholders %IR%, %LOG%, %DONE% and
  %PROGRESS% are replaced by Convi before the script is started.

  Every command is executed inside try/except so a single bad object never
  stops the conversion or opens a modal dialog; problems go to the log.
  ========================================================================== }

var
  GLog       : TStringList;
  GLogDirty  : Integer;
  OnlineDrcWas : Integer;     { PCB builds: the user's online DRC setting, put back at the end (-1 unknown) }
  F          : TStringList;     { fields of the current line }
  Lines      : TStringList;
  LineNo     : Integer;
  ErrCount   : Integer;
  Mode       : String;          { 'PCB' or 'LIB' }
  Doc        : Variant;         { IServerDocument }
  Board      : Variant;         { IPCB_Board (also the library board) }
  PcbLib     : Variant;
  CurComp    : Variant;
  CurLibComp : Variant;
  InComp     : Boolean;
  FirstLibFp : Boolean;
  DefaultLibComp : Variant;
  OffX, OffY : Integer;
  Nets       : TInterfaceList;
  NetNames   : TStringList;
  CurClass   : Variant;
  Polys      : TInterfaceList;
  { region state }
  RegObj     : Variant;
  RegGP      : Variant;
  RegC       : Variant;
  RegPts     : Integer;
  RegHole    : Boolean;
  InRegion   : Boolean;
  { polygon / outline state }
  InPoly     : Boolean;
  PolyObj    : Variant;
  PolyPts    : TStringList;
  OutlineSegs: TStringList;
  InOutline  : Boolean;
  CopperObjs : TInterfaceList;
  Stat       : TStringList;

{ ------------------------------------------------------------------ logging }
procedure FlushLog;
begin
  GLog.SaveToFile('%LOG%');
  GLogDirty := 0;
end;

procedure Log(S: String);
begin
  GLog.Add(S);
  GLogDirty := GLogDirty + 1;
  if GLogDirty > 200 then FlushLog;
end;

procedure Err(S: String);
begin
  ErrCount := ErrCount + 1;
  if ErrCount < 2000 then
    Log('ERROR line ' + IntToStr(LineNo + 1) + ': ' + S);
end;

procedure Progress;
var P: TStringList;
begin
  P := TStringList.Create;
  P.Add(IntToStr(LineNo) + '/' + IntToStr(Lines.Count));
  P.SaveToFile('%PROGRESS%');
  P.Free;
end;

procedure CountStat(K: String);
var i: Integer;
begin
  i := Stat.IndexOfName(K);
  if i < 0 then Stat.Add(K + '=1')
  else Stat.ValueFromIndex[i] := IntToStr(StrToInt(Stat.ValueFromIndex[i]) + 1);
end;

{ ------------------------------------------------------------------ fields }
function Unesc(S: String): String;
var i: Integer; R: String; H: String;
begin
  if Pos('%', S) = 0 then
  begin
    Result := S;
    Exit;
  end;
  R := '';
  i := 1;
  while i <= Length(S) do
  begin
    if (S[i] = '%') and (i + 2 <= Length(S)) then
    begin
      H := Copy(S, i + 1, 2);
      R := R + Chr(StrToInt('$' + H));
      i := i + 3;
    end
    else
    begin
      R := R + S[i];
      i := i + 1;
    end;
  end;
  Result := R;
end;

function V(K: String): String;
begin
  Result := Unesc(F.Values[K]);
end;

function Has(K: String): Boolean;
begin
  Result := F.IndexOfName(K) >= 0;
end;

function VI(K: String; D: Integer): Integer;
var S: String;
begin
  S := F.Values[K];
  if S = '' then Result := D else Result := StrToInt(S);
end;

function VD(K: String; D: Double): Double;
var S: String;
begin
  S := F.Values[K];
  if S = '' then Result := D else Result := StrToInt(S) / 1000.0;
end;

function VB(K: String): Boolean;
begin
  Result := F.Values[K] = '1';
end;

function CX(K: String): Integer;
begin
  Result := StrToInt(F.Values[K]) + OffX;
end;

function CY(K: String): Integer;
begin
  Result := StrToInt(F.Values[K]) + OffY;
end;

