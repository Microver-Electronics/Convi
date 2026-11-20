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
var D, B, It, O, C: Variant; Lst, Cp: TInterfaceList; i, best: Integer; P: String;
begin
  P := '%OUT%\One-Air-Max\One-Air-Max.PcbDoc';
  D := Client.OpenDocument('PCB', P);
  D.SetModified(False);
  Client.CloseDocument(D);
  D := Client.OpenDocument('PCB', P);
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  L('before: ' + CountViol(B));
  Lst := TInterfaceList.Create;
  Cp := TInterfaceList.Create;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do begin Lst.Add(O); O := It.NextPCBObject; end;
  B.BoardIterator_Destroy(It);
  PCBServer.PreProcess;
  for i := 0 to Lst.Count - 1 do
  begin
    O := Lst.Items[i];
    C := O.Replicate;
    C.Name := O.Name;
    C.PourIndex := O.PourIndex;
    Cp.Add(C);
    B.RemovePCBObject(O);
  end;
  PCBServer.PostProcess;
  L('removed ' + IntToStr(Lst.Count));
  while Cp.Count > 0 do
  begin
    best := 0;
    for i := 1 to Cp.Count - 1 do
      if Cp.Items[i].PourIndex < Cp.Items[best].PourIndex then best := i;
    C := Cp.Items[best];
    B.AddPCBObject(C);
    C.SetState_CopperPourInvalid;
    C.Rebuild;
    C.SetState_CopperPourValid;
    Cp.Delete(best);
  end;
  L('after recreate+ordered pour: ' + CountViol(B));
  D.SetModified(False);
end;
