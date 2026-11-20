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
    else nU := nU + 1;
    O := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  Result := 'short=' + IntToStr(nS) + ' clear=' + IntToStr(nC) + ' other=' + IntToStr(nU);
end;

procedure Repour(B: Variant; Mode: Integer);
var It, O: Variant;
begin
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do
  begin
    if Mode = 1 then O.ClipAcuteCorners := False;
    if Mode = 2 then O.MitreCorners := False;
    if Mode = 3 then O.RemoveIslandsByArea := False;
    if Mode = 4 then O.UseOctagons := False;
    if Mode = 9 then L(O.Name + ' clip=' + BoolToStr(O.ClipAcuteCorners, True) + ' mitre=' + BoolToStr(O.MitreCorners, True) + ' islArea=' + BoolToStr(O.RemoveIslandsByArea, True) + ' thr=' + FloatToStr(O.IslandAreaThreshold) + ' oct=' + BoolToStr(O.UseOctagons, True) + ' arcmode=' + IntToStr(O.ArcPourMode) + ' avoid=' + BoolToStr(O.AvoidObsticles, True));
    O.SetState_CopperPourInvalid;
    O.Rebuild;
    O.SetState_CopperPourValid;
    O := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;

procedure Body;
var D, B: Variant; i: Integer; P: String;
begin
  P := '%OUT%\batch\board\board.PcbDoc';
  for i := 0 to 4 do
  begin
    D := Client.OpenDocument('PCB', P);
    D.SetModified(False);
    Client.CloseDocument(D);
    D := Client.OpenDocument('PCB', P);
    Client.ShowDocument(D);
    B := PCBServer.GetCurrentPCBBoard;
    if i = 0 then Repour(B, 9);
    Repour(B, i);
    L('fresh mode ' + IntToStr(i) + ': ' + CountViol(B));
    D.SetModified(False);
  end;
end;
