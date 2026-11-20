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

procedure SetClear(B: Variant; Mils: Double);
var It, R: Variant;
begin
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eRuleObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  R := It.FirstPCBObject;
  while R <> Nil do
  begin
    if R.RuleKind = eRule_Clearance then R.Gap := MilsToCoord(Mils);
    R := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;

procedure AddPolyRule(B: Variant; Mils: Double);
var R: Variant;
begin
  R := PCBServer.PCBRuleFactory(eRule_Clearance);
  R.Name := 'Convi_ZoneClearance';
  R.Scope1Expression := 'IsPolygon or InPolygon';
  R.Scope2Expression := 'All';
  R.Gap := MilsToCoord(Mils);
  B.AddPCBObject(R);
  L('rule prio ' + IntToStr(R.Priority));
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
    SetClear(B, 5.866);
    if i = 1 then AddPolyRule(B, 5.787);
    if i = 2 then AddPolyRule(B, 5.827);
    if i = 3 then AddPolyRule(B, 5.846);
    Repour(B, 0);
    L('fresh mode ' + IntToStr(i) + ': ' + CountViol(B));
    D.SetModified(False);
  end;
end;
