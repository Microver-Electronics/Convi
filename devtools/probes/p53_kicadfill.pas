function CountViol(B: Variant): String;
var It, O: Variant; nS, nC, nU: Integer;
begin
  B.RunBatchDesignRuleCheck('%ROOT%\devtools\_probe_run\drc34.txt', eDRC_Text, False, False);
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eViolationObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject; nS := 0; nC := 0; nU := 0;
  while O <> Nil do
  begin
    if O.Rule.RuleKind = eRule_ShortCircuit then nS := nS + 1
    else if O.Rule.RuleKind = eRule_Clearance then nC := nC + 1
    else begin nU := nU + 1; if nU < 12 then L('  ' + O.Rule.Name + ': ' + O.Description); end;
    O := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  Result := 'short=' + IntToStr(nS) + ' clear=' + IntToStr(nC) + ' other=' + IntToStr(nU);
end;

function FindPoly(B: Variant; Nm: String): Variant;
var It, G: Variant;
begin
  Result := Nil;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  G := It.FirstPCBObject;
  while G <> Nil do
  begin
    if G.Name = Nm then Result := G;
    G := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;

procedure Body;
var D, B, G, R, GP, C: Variant; F, Pts: TStringList; i, j, k, n: Integer; Nm, S, Done: String;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  D.SetModified(False);
  Client.CloseDocument(D);
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  L('before: ' + CountViol(B));
  F := TStringList.Create;
  F.LoadFromFile('%ROOT%\devtools\_probe_run\fills.txt');
  Pts := TStringList.Create;
  Pts.Delimiter := ' '; Pts.StrictDelimiter := True;
  Done := '|';
  PCBServer.PreProcess;
  for i := 0 to F.Count - 1 do
  begin
    S := F[i];
    k := Pos(#9, S);
    if k = 0 then Continue;
    Nm := Copy(S, 1, k - 1);
    G := FindPoly(B, Nm);
    if G = Nil then Continue;
    if Pos('|' + Nm + '|', Done) = 0 then
    begin
      G.FreePrimitives;
      Done := Done + Nm + '|';
    end;
    Pts.DelimitedText := Copy(S, k + 1, Length(S));
    C := PCBServer.PCBContourFactory;
    for j := 0 to Pts.Count - 1 do
    begin
      n := Pos(',', Pts[j]);
      C.AddPoint(StrToInt(Copy(Pts[j], 1, n - 1)), StrToInt(Copy(Pts[j], n + 1, 20)));
    end;
    GP := PCBServer.PCBGeometricPolygonFactory;
    GP.AddContourIsHole(C, False);
    R := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    R.Layer := G.Layer;
    R.Kind := eRegionKind_Copper;
    R.Net := G.Net;
    R.SetGeometricPolygon(GP);
    G.AddPCBObject(R);
  end;
  PCBServer.PostProcess;
  L('replaced: ' + Done);
  L('after: ' + CountViol(B));
  D.SetModified(False);
end;
