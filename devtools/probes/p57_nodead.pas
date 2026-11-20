procedure Body;
var D, B, It, G, GI, R: Variant; n: Integer; A: Double; P: String;
begin
  P := '%OUT%\batch\One-Air-Max\One-Air-Max.PcbDoc';
  D := Client.OpenDocument('PCB', P);
  B := PCBServer.GetPCBBoardByPath(P);
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  G := It.FirstPCBObject;
  while G <> Nil do
  begin
    G.RemoveDead := False;
    G.SetState_CopperPourInvalid;
    G.Rebuild;
    G.SetState_CopperPourValid;
    n := 0; A := 0;
    GI := G.GroupIterator_Create;
    GI.AddFilter_ObjectSet(MkSet(eRegionObject));
    R := GI.FirstPCBObject;
    while R <> Nil do
    begin
      n := n + 1;
      A := A + R.Area;
      R := GI.NextPCBObject;
    end;
    G.GroupIterator_Destroy(GI);
    L(G.Name + #9 + IntToStr(n) + #9 + FloatToStr(A));
    G := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  D.SetModified(False);
end;
