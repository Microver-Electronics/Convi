function CountViol(B: Variant): String;
var It, O: Variant; nS, nC, nU: Integer;
begin
  B.RunBatchDesignRuleCheck('%ROOT%\devtools\_probe_run\drc60.txt', eDRC_Text, False, False);
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eViolationObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject; nS := 0; nC := 0; nU := 0;
  while O <> Nil do
  begin
    if O.Rule.RuleKind = eRule_ShortCircuit then nS := nS + 1
    else if Pos('UnRouted', O.Rule.Name) > 0 then nU := nU + 1
    else nC := nC + 1;
    O := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  Result := 'short=' + IntToStr(nS) + ' unrouted=' + IntToStr(nU) + ' other=' + IntToStr(nC);
end;

procedure Body;
var D, B, It, O: Variant; Lst: TInterfaceList; i, best: Integer; P: String;
begin
  P := '%OUT%\batch\One-Air-Max\One-Air-Max.PcbDoc';
  D := Client.OpenDocument('PCB', P);
  D.SetModified(False);
  Client.CloseDocument(D);
  D := Client.OpenDocument('PCB', P);
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  L('before: ' + CountViol(B));
  Lst := TInterfaceList.Create;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do begin if O.Poured then Lst.Add(O); O := It.NextPCBObject; end;
  B.BoardIterator_Destroy(It);
  for i := 0 to Lst.Count - 1 do Lst.Items[i].FreePrimitives;
  while Lst.Count > 0 do
  begin
    best := 0;
    for i := 1 to Lst.Count - 1 do
      if Lst.Items[i].PourIndex < Lst.Items[best].PourIndex then best := i;
    O := Lst.Items[best];
    O.SetState_CopperPourInvalid;
    O.Rebuild;
    O.SetState_CopperPourValid;
    Lst.Delete(best);
  end;
  L('after ordered pour: ' + CountViol(B));
  L('empty: ' + IntToStr(0));
  D.SetModified(False);
end;
