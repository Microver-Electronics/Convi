procedure Main;
var Done, Started: TStringList; S, Cmd: String; T0: TDateTime;
begin
  Started := TStringList.Create;
  Started.Add('started');
  Started.SaveToFile('%STARTED%');
  GLog := TStringList.Create;
  GLogDirty := 0;
  Stat := TStringList.Create;
  F := TStringList.Create;
  F.Delimiter := #9;
  F.StrictDelimiter := True;
  F.QuoteChar := #1;
  Nets := TInterfaceList.Create;
  NetNames := TStringList.Create;
  Polys := TInterfaceList.Create;
  PolyPts := TStringList.Create;
  OutlineSegs := TStringList.Create;
  InitSpecific;
  InComp := False; InRegion := False; InPoly := False; InOutline := False;
  ErrCount := 0;
  T0 := Now;
  Lines := TStringList.Create;
  try
    Lines.LoadFromFile('%IR%');
    Log('IR lines: ' + IntToStr(Lines.Count));
    for LineNo := 0 to Lines.Count - 1 do
    begin
      S := Lines[LineNo];
      if (S = '') or (S[1] = '#') then Continue;
      F.DelimitedText := S;
      Cmd := F[0];
      try
        Exec(Cmd, S);
      except
        Err(Cmd + ': ' + ExceptionMessage);
      end;
      if (LineNo mod 2000) = 0 then Progress;
      if (LineNo > 0) and ((LineNo mod 50000) = 0) then
        Log('line ' + IntToStr(LineNo) + ' at ' + IntToStr(Round((Now - T0) * 86400)) + ' s');
    end;
  except
    Log('FATAL: ' + ExceptionMessage);
  end;
  try
    FinishSpecific;
  except
  end;
  Log('errors: ' + IntToStr(ErrCount));
  Log('stats: ' + Stat.CommaText);
  Log('seconds: ' + IntToStr(Round((Now - T0) * 86400)));
  FlushLog;
  Done := TStringList.Create;
  Done.Add('done');
  Done.SaveToFile('%DONE%');
end;
