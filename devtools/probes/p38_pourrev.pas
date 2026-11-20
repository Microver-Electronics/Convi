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

procedure PourSorted(B: Variant);
var It, O: Variant; Lst: TInterfaceList; k, i, best: Integer;
begin
  Lst := TInterfaceList.Create;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do begin Lst.Add(O); O := It.NextPCBObject; end;
  B.BoardIterator_Destroy(It);
  { pour lowest PourIndex first }
  while Lst.Count > 0 do
  begin
    best := 0;
    for i := 1 to Lst.Count - 1 do
      if Lst.Items[i].PourIndex > Lst.Items[best].PourIndex then best := i;
    O := Lst.Items[best];
    L('pour ' + O.Name + ' idx=' + IntToStr(O.PourIndex));
    O.SetState_CopperPourInvalid;
    O.Rebuild;
    O.SetState_CopperPourValid;
    Lst.Delete(best);
  end;
end;

procedure Body;
var D, B: Variant; i: Integer; P: String;
begin
  P := '%OUT%\batch\board\board.PcbDoc';
  D := Client.OpenDocument('PCB', P);
  D.SetModified(False);
  Client.CloseDocument(D);
  D := Client.OpenDocument('PCB', P);
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  L('fresh: ' + CountViol(B));
  for i := 1 to 3 do
  begin
    PourSorted(B);
    L('sorted pass ' + IntToStr(i) + ': ' + CountViol(B));
  end;
  D.SetModified(False);
end;
