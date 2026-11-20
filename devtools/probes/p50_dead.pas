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
    if Mode = 1 then O.RemoveDead := False;
    if Mode = 2 then O.MinTrack := MilsToCoord(1);
    if Mode = 3 then O.ArcApproximation := MilsToCoord(0.01);
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
  for i := 0 to 3 do
  begin
    D := Client.OpenDocument('PCB', P);
    D.SetModified(False);
    Client.CloseDocument(D);
    D := Client.OpenDocument('PCB', P);
    Client.ShowDocument(D);
    B := PCBServer.GetCurrentPCBBoard;
    Repour(B, i);
    L('fresh mode ' + IntToStr(i) + ': ' + CountViol(B));
    D.SetModified(False);
  end;
end;
