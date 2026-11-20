{ Convi: dump PCB geometry (pads, tracks, arcs, vias, polygons) and run DRC.
  Placeholders: %PCB% %OUT% %LOG% %DONE% %STARTED% %DRC% %DRCREPORT% }

var GLog: TStringList;

procedure Log(S: String);
begin
  GLog.Add(S);
  GLog.SaveToFile('%LOG%');
end;

function NetName(O: Variant): String;
begin
  Result := '';
  if O.Net <> Nil then Result := O.Net.Name;
end;

function ContourArea(C: Variant): Double;
var i, n, j: Integer; S: Double;
begin
  S := 0;
  n := C.Count;
  for i := 0 to n - 1 do
  begin
    j := (i + 1) mod n;
    S := S + (C.x[i] * 1.0) * C.y[j] - (C.x[j] * 1.0) * C.y[i];
  end;
  Result := Abs(S) / 2;
end;

{ poured copper of a solid polygon in mm2 x 1000 (its regions less their holes); -1 when unreadable }
function PolyCopper(P: Variant): Integer;
var GI, R: Variant; A: Double; k: Integer;
begin
  Result := -1;
  try
    A := 0;
    GI := P.GroupIterator_Create;
    GI.AddFilter_ObjectSet(MkSet(eRegionObject));
    R := GI.FirstPCBObject;
    while R <> Nil do
    begin
      A := A + ContourArea(R.MainContour);
      for k := 0 to R.HoleCount - 1 do
        A := A - ContourArea(R.Holes[k]);
      R := GI.NextPCBObject;
    end;
    P.GroupIterator_Destroy(GI);
    { internal units: 10000 per mil }
    Result := Round(A * 0.00000000000645160 * 1000);
  except
    Result := -1;
  end;
end;

procedure SetUnrouted(B: Variant; Enable: Boolean);
var It, R: Variant;
begin
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eRuleObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  R := It.FirstPCBObject;
  while R <> Nil do
  begin
    if Pos('UnRouted', R.Name) > 0 then R.DRCEnabled := Enable;
    R := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;

procedure Main;
var D, B, It, O, C: Variant; Lst, Done: TStringList; S: String; n: Integer;
begin
  Done := TStringList.Create;
  Done.Add('started');
  Done.SaveToFile('%STARTED%');
  GLog := TStringList.Create;
  Lst := TStringList.Create;
  try
    // start from the saved file: a board left open by an earlier pass (DRC markers, re-added polygons)
    // poured with stale rule matches and ran no DRC (zone clearances lost, a pour emptied)
    try
      D := Client.GetDocumentByPath('%PCB%');
      if D <> Nil then
      begin
        D.SetModified(False);
        Client.CloseDocument(D);
      end;
    except
    end;
    D := Client.OpenDocument('PCB', '%PCB%');
    Client.ShowDocument(D);
    B := PCBServer.GetPCBBoardByPath('%PCB%');
    if B = Nil then B := PCBServer.GetCurrentPCBBoard;
    Lst.Add('O' + #9 + IntToStr(B.XOrigin) + #9 + IntToStr(B.YOrigin));
    It := B.BoardIterator_Create;
    It.AddFilter_ObjectSet(MkSet(ePadObject, eTrackObject, eArcObject, eViaObject, ePolyObject));
    It.AddFilter_LayerSet(AllLayers);
    It.AddFilter_Method(eProcessAll);
    O := It.FirstPCBObject;
    n := 0;
    while O <> Nil do
    begin
      if O.ObjectId = ePadObject then
      begin
        S := '';
        if O.InComponent then S := O.Component.Name.Text;
        Lst.Add('P' + #9 + S + #9 + O.Name + #9 + IntToStr(O.X) + #9 + IntToStr(O.Y) + #9 +
                IntToStr(Round(O.Rotation * 1000)) + #9 + IntToStr(O.Layer) + #9 + NetName(O) + #9 +
                IntToStr(O.TopXSize) + #9 + IntToStr(O.TopYSize) + #9 + IntToStr(O.HoleSize));
      end
      else if O.ObjectId = eTrackObject then
      begin
        if not O.InComponent then
          Lst.Add('T' + #9 + IntToStr(O.X1) + #9 + IntToStr(O.Y1) + #9 + IntToStr(O.X2) + #9 + IntToStr(O.Y2) + #9 +
                  IntToStr(O.Width) + #9 + IntToStr(O.Layer) + #9 + NetName(O));
      end
      else if O.ObjectId = eArcObject then
      begin
        if not O.InComponent then
          Lst.Add('A' + #9 + IntToStr(O.XCenter) + #9 + IntToStr(O.YCenter) + #9 + IntToStr(O.Radius) + #9 +
                  IntToStr(Round(O.StartAngle * 1000)) + #9 + IntToStr(Round(O.EndAngle * 1000)) + #9 +
                  IntToStr(O.LineWidth) + #9 + IntToStr(O.Layer) + #9 + NetName(O));
      end
      else if O.ObjectId = eViaObject then
        Lst.Add('V' + #9 + IntToStr(O.X) + #9 + IntToStr(O.Y) + #9 + IntToStr(O.Size) + #9 + IntToStr(O.HoleSize) + #9 +
                IntToStr(O.LowLayer) + #9 + IntToStr(O.HighLayer) + #9 + NetName(O))
      else if O.ObjectId = ePolyObject then
        Lst.Add('G' + #9 + O.Name + #9 + IntToStr(O.Layer) + #9 + NetName(O) + #9 + FloatToStr(O.AreaSize) + #9 +
                IntToStr(PolyCopper(O)));
      n := n + 1;
      O := It.NextPCBObject;
    end;
    B.BoardIterator_Destroy(It);
    Log('objects=' + IntToStr(n));
    if '%DRC%' = '1' then
    begin
      { an unrouted board would fill Altium's 500-violation limit with un-routed nets }
      if '%NOUNROUTED%' = '1' then SetUnrouted(B, False);
      // a run that writes no report did not check anything (seen once after a re-pour: 0 violations,
      // the old report left in place): delete it first, and run again with the board shown if it is missing
      if FileExists('%DRCREPORT%') then DeleteFile('%DRCREPORT%');
      B.RunBatchDesignRuleCheck('%DRCREPORT%', eDRC_Text, False, False);
      if not FileExists('%DRCREPORT%') then
      begin
        Log('drc: no report, run again');
        Client.ShowDocument(D);
        B := PCBServer.GetCurrentPCBBoard;
        B.RunBatchDesignRuleCheck('%DRCREPORT%', eDRC_Text, False, False);
        if not FileExists('%DRCREPORT%') then Log('drc: not run');
      end;
      if '%NOUNROUTED%' = '1' then SetUnrouted(B, True);
      It := B.BoardIterator_Create;
      It.AddFilter_ObjectSet(MkSet(eViolationObject));
      It.AddFilter_LayerSet(AllLayers);
      It.AddFilter_Method(eProcessAll);
      O := It.FirstPCBObject;
      n := 0;
      while O <> Nil do
      begin
        S := '';
        try
          S := O.Rule.Name + #9 + IntToStr(O.Rule.RuleKind) + #9 + O.Description;
        except
          S := O.Name;
        end;
        Lst.Add('X' + #9 + S);
        n := n + 1;
        O := It.NextPCBObject;
      end;
      B.BoardIterator_Destroy(It);
      Log('violations=' + IntToStr(n));
    end;
    D.SetModified(False);
    Client.CloseDocument(D);
  except
    Log('FATAL: ' + ExceptionMessage);
  end;
  Lst.SaveToFile('%OUT%');
  Done.SaveToFile('%DONE%');
end;
